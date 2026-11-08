#!/usr/bin/env python3
"""Install sing-box into a project-relative directory (vendor/sing-box/<platform>-<arch>/).

Goals:
- Relative paths only; one config shared by Windows / Linux / macOS.
- Nothing is written to the system PATH or system directories.
- Archives are cached in vendor/sing-box/.cache/ with resume support, so the
  script can be re-run safely.
- GitHub first; gh-proxy mirrors as fallback when direct access fails.
- Version resolution via the GitHub API, falling back to the
  releases/latest redirect when the API is rate-limited.

Examples:
    python scripts/install_singbox.py
    python scripts/install_singbox.py --version v1.14.4 --force
    python scripts/install_singbox.py --platform linux --arch amd64
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = "vendor/sing-box"
CACHE_DIR = ".cache"
API_LATEST = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"
LATEST_REDIRECT = "https://github.com/SagerNet/sing-box/releases/latest"
ASSET_BASE = "https://github.com/SagerNet/sing-box/releases/download/{tag}/{name}"
MIRRORS = ("https://gh-proxy.com/", "https://ghproxy.net/")
USER_AGENT = "anonchains-install/1.0"

_SYSTEM = {"windows": "windows", "linux": "linux", "darwin": "darwin"}
_ARCH = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "i386": "386",
    "i686": "386",
    "armv7l": "armv7",
    "armv6l": "armv6",
    "riscv64": "riscv64",
}


def detect_platform() -> str:
    system = platform.system().lower()
    return _SYSTEM.get(system, system)


def detect_arch() -> str:
    machine = platform.machine().lower()
    return _ARCH.get(machine, machine)


def normalize_tag(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    return value if value.startswith("v") else f"v{value}"


def _retry(action, attempts: int = 3, delay: float = 2.0):
    """Retry network actions a limited number of times before giving up."""
    last: Exception | None = None
    for index in range(attempts):
        try:
            return action()
        except Exception as exc:
            last = exc
            print(f"网络请求失败（{index + 1}/{attempts}）：{exc}")
            if index + 1 < attempts:
                time.sleep(delay * (index + 1))
    if last is None:
        raise RuntimeError("重试全部失败但未捕获到异常")
    raise last


def _api_latest() -> str:
    request = urllib.request.Request(
        API_LATEST,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise RuntimeError("GitHub API 未返回 tag_name")
    return tag


def _latest_tag_from_redirect() -> str:
    request = urllib.request.Request(LATEST_REDIRECT, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as resp:
        final = resp.geturl()
    match = re.search(r"/releases/tag/(v[0-9][^/?#]*)", final)
    if not match:
        raise RuntimeError(f"无法从跳转地址解析版本号: {final}")
    return match.group(1)


def resolve_tag(version: str) -> str:
    if version:
        return normalize_tag(version)
    try:
        return _retry(_api_latest)
    except Exception as exc:
        print(f"GitHub API 不可用（{exc}），改用 releases/latest 跳转解析版本")
        return _retry(_latest_tag_from_redirect)


def asset_info(tag: str, platform_name: str, arch: str) -> tuple[str, str]:
    version = tag.lstrip("v")
    suffix = ".zip" if platform_name == "windows" else ".tar.gz"
    name = f"sing-box-{version}-{platform_name}-{arch}{suffix}"
    return name, ASSET_BASE.format(tag=tag, name=name)


def _head_size(url: str) -> int:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as resp:
        return int(resp.headers.get("Content-Length") or 0)


def _range_size(url: str) -> int:
    """Read Content-Range from a 1-byte Range request to learn the file size."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"}
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        match = re.search(r"/\s*(\d+)", resp.headers.get("Content-Range", "") or "")
        return int(match.group(1)) if match else 0


def remote_size(url: str) -> int:
    for probe in (_head_size, _range_size):
        try:
            size = probe(url)
            if size:
                return size
        except Exception:
            continue
    return 0


def archive_integrity(path: Path, expected: int) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if expected and path.stat().st_size < expected:
        return False
    try:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                return zf.testzip() is None
        with tarfile.open(path, "r:gz") as tf:
            tf.getmembers()
        return True
    except Exception:
        return False


def _curl_download(url: str, target: Path) -> bool:
    base = [
        "curl",
        "-L",
        "--retry",
        "4",
        "--retry-all-errors",
        "--retry-delay",
        "3",
        "--connect-timeout",
        "20",
        "--speed-time",
        "120",
        "--speed-limit",
        "500",
    ]
    resume = target.exists() and target.stat().st_size > 0
    commands = []
    if resume:
        commands.append(base + ["-C", "-", "--progress-bar", "-o", str(target), url])
    commands.append(base + ["--progress-bar", "-o", str(target), url])
    for cmd in commands:
        result = subprocess.run(cmd, check=False)
        if result.returncode == 0 and target.exists() and target.stat().st_size > 0:
            return True
    return False


def _urllib_download(url: str, target: Path) -> None:
    headers = {"User-Agent": USER_AGENT}
    offset = target.stat().st_size if target.exists() else 0
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as resp:
        mode = "wb"
        if offset and getattr(resp, "status", 200) == 206:
            mode = "ab"
        with target.open(mode) as fh:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)


def download(url: str, target: Path, expected: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if archive_integrity(target, expected):
        print(f"复用缓存：{target.relative_to(ROOT)}")
        return

    attempts = [("直连 GitHub", url)] + [(f"镜像 {mirror}", mirror + url) for mirror in MIRRORS]
    for index, (label, source) in enumerate(attempts):
        print(f"尝试{label}（{index + 1}/{len(attempts)}）")
        if shutil.which("curl"):
            if _curl_download(source, target) and archive_integrity(target, expected):
                return
            print("curl 下载未完成，继续尝试下一个源")
        try:
            _retry(lambda: _urllib_download(source, target))
        except Exception as exc:
            print(f"内置下载器失败：{exc}")
        if archive_integrity(target, expected):
            return
    raise RuntimeError("所有下载源均失败，请检查网络后重试")


def extract_binary(archive: Path, dest_dir: Path, binary_name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    output = dest_dir / binary_name
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if Path(info.filename).name == binary_name and not info.is_dir():
                    with zf.open(info) as src, output.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    break
            else:
                raise RuntimeError(f"压缩包内未找到 {binary_name}")
    else:
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                if Path(member.name).name == binary_name and member.isfile():
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    with src, output.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    break
            else:
                raise RuntimeError(f"压缩包内未找到 {binary_name}")
    if os.name != "nt":
        output.chmod(output.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="安装 sing-box 到项目内的相对目录")
    parser.add_argument("--version", default="", help="指定 release tag（如 v1.14.4），默认最新版")
    parser.add_argument("--platform", default="", help="目标平台：windows / linux / darwin，默认当前系统")
    parser.add_argument("--arch", default="", help="目标架构：amd64 / arm64 / 386 / armv7，默认当前架构")
    parser.add_argument("--dest", default=DEFAULT_DEST, help="相对项目根目录的安装目录")
    parser.add_argument("--force", action="store_true", help="已存在时覆盖")
    args = parser.parse_args()

    platform_name = args.platform or detect_platform()
    arch = args.arch or detect_arch()
    dest_root = Path(args.dest)
    if not dest_root.is_absolute():
        dest_root = ROOT / dest_root
    dest_dir = dest_root / f"{platform_name}-{arch}"

    binary_name = "sing-box.exe" if platform_name == "windows" else "sing-box"
    if (dest_dir / binary_name).exists() and not args.force:
        print(f"已存在：{dest_dir / binary_name}（如需覆盖请加 --force）")
        return 0

    tag = resolve_tag(args.version)
    name, url = asset_info(tag, platform_name, arch)
    print(f"版本: {tag}")
    print(f"资源: {name}")
    archive = dest_root / CACHE_DIR / name
    expected = remote_size(url)
    if expected:
        print(f"大小: {expected} 字节")

    download(url, archive, expected)
    binary = extract_binary(archive, dest_dir, binary_name)

    (dest_root / "VERSION").write_text(f"{tag}\n", encoding="utf-8")
    print(f"已安装：{binary}")
    config_hint = f"{DEFAULT_DEST}/{platform_name}-{arch}/{binary_name}".replace("\\", "/")
    print("提示：把路径写入配置即可生效，例如：")
    print(f"  anonchains config-set core sing_box_path {config_hint}")
    print("或在 CLI 主菜单 4 -> 10 自动完成。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消")
        raise SystemExit(130)
    except Exception as exc:
        print(f"安装失败：{exc}")
        raise SystemExit(1)
