"""一次性脚本：为两个插件仓库创建 GitHub Release 并上传 zip。"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"

DIST = Path(r"D:\tmp\plugin-registry\dist")

RELEASES = [
    {
        "repo": "ovonull/video-parser",
        "tag": "v1.3.2",
        "name": "v1.3.2",
        "zip": DIST / "cn.ucbk.video-1.3.2.zip",
        "body": (
            "## 更新内容\n\n"
            "- **新增：消息接管声明**。判定出是视频链接后立刻调用 `host.claim_message()` 抢占处理权，"
            "AI 不再对同一条消息重复回复。支持多平台解析的插件同时启用时，同一条链接只有一个插件会处理。\n"
            "- **新增：AI 评价（可选）**。视频发完后可让机器人 AI 补一句点评，"
            "需要在插件配置里打开开关，并在审核时授予「调用机器人 AI」能力。提示词可自定义，默认给 1-10 分评分。\n\n"
            "## 安装说明\n\n"
            "- 需要宿主支持 `reply.claim`（NapCatQQ Service 2026-10-21 之后的版本）；老版本会自动忽略该能力。\n"
            "- 附件 `cn.ucbk.video-1.3.2.zip` 可直接上传到插件库，或在插件库按 tag `v1.3.2` 同步。\n"
        ),
    },
    {
        "repo": "ovonull/weather",
        "tag": "v1.1.1",
        "name": "v1.1.1",
        "zip": DIST / "cn.ucbk.weather-1.1.1.zip",
        "body": (
            "## 更新内容\n\n"
            "- **新增：消息接管声明**。判定出是天气查询后立刻调用 `host.claim_message()` 抢占处理权，"
            "AI 不再对同一条消息重复回复。\n\n"
            "## 安装说明\n\n"
            "- 需要宿主支持 `reply.claim`（NapCatQQ Service 2026-10-21 之后的版本）；老版本会自动忽略该能力。\n"
            "- 附件 `cn.ucbk.weather-1.1.1.zip` 可直接上传到插件库，或在插件库按 tag `v1.1.1` 同步。\n"
        ),
    },
]


def get_token() -> str:
    result = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("取不到 GitHub 凭据")


def api(token: str, path: str, method: str = "GET", payload=None, raw: bytes = None,
        content_type: str = "application/json"):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "plugin-release-script",
    }
    data = None
    if raw is not None:
        data = raw
        headers["Content-Type"] = content_type
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        "https://api.github.com" + path, data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        return error.code, error.read(400).decode("utf-8", "ignore")


def ensure_release(token: str, item: dict) -> dict | None:
    repo = item["repo"]
    status, existing = api(token, f"/repos/{repo}/releases/tags/{item['tag']}")
    if status == 200:
        print(f"  已存在 Release：{item['tag']}（更新说明）")
        status, updated = api(
            token,
            f"/repos/{repo}/releases/{existing['id']}",
            method="PATCH",
            payload={"name": item["name"], "body": item["body"]},
        )
        return updated if status == 200 else existing

    status, created = api(
        token,
        f"/repos/{repo}/releases",
        method="POST",
        payload={
            "tag_name": item["tag"],
            "name": item["name"],
            "body": item["body"],
            "draft": False,
            "prerelease": False,
        },
    )
    if status not in (200, 201):
        print(f"  创建失败：{status} {created}")
        return None
    print(f"  已创建 Release：{item['tag']}")
    return created


def upload_asset(token: str, item: dict, release: dict) -> None:
    zip_path: Path = item["zip"]
    if not zip_path.is_file():
        print(f"  跳过附件：{zip_path} 不存在")
        return
    name = zip_path.name
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            print(f"  附件已存在：{name}")
            return
    # 上传走 uploads.github.com，和 api.github.com 不是同一个域名，单独构造。
    request = urllib.request.Request(
        f"https://uploads.github.com/repos/{item['repo']}/releases/{release['id']}/assets?name={name}",
        data=zip_path.read_bytes(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/zip",
            "Accept": "application/vnd.github+json",
            "User-Agent": "plugin-release-script",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            print(f"  已上传附件：{name}（{response.status}）")
    except urllib.error.HTTPError as error:
        print(f"  附件上传失败：{error.code} {error.read(200).decode('utf-8', 'ignore')}")


def main() -> int:
    token = get_token()
    for item in RELEASES:
        print(f"[{item['repo']}] {item['tag']}")
        release = ensure_release(token, item)
        if release is None:
            continue
        upload_asset(token, item, release)
        print(f"  地址：https://github.com/{item['repo']}/releases/tag/{item['tag']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
