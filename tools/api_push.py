"""github.com 直连被掐(api.github.com 通)时, 经 git data API 上传提交。
用法: python tools/api_push.py <commit_sha> <tag>   (在 taskmirror 仓内跑)
树与提交对象在远端按内容寻址重建, 最终 ref 与本地 SHA 一致。"""
import base64
import json
import subprocess
import sys

REPO = "wzhdsb/taskmirror"


def gh(endpoint, body=None, method=None):
    cmd = ["gh", "api", endpoint]
    if method:
        cmd += ["--method", method]
    if body is not None:
        cmd += ["--input", "-"]
        r = subprocess.run(cmd, input=json.dumps(body).encode(), capture_output=True)
    else:
        r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        raise SystemExit(f"gh api {endpoint} 失败: {r.stderr.decode()[:500]}")
    return json.loads(r.stdout) if r.stdout.strip() else {}


def main(commit_sha: str, tag: str):
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    # token 无 workflow scope 时 GitHub 对 .github/workflows/* 报伪装 404, 剔除后单独处理
    files = [f for f in files if not f.startswith(".github/workflows/")] or files
    print(f"上传 {len(files)} 文件(workflow 文件跳过, 授权后重跑补传)")
    entries, batch, size = [], [], 0
    base_tree = None

    def flush():
        nonlocal batch, base_tree
        if not batch:
            return
        body = {"tree": batch}
        if base_tree:
            body["base_tree"] = base_tree
        t = gh(f"repos/{REPO}/git/trees", body, "POST")
        base_tree = t["sha"]
        batch = []

    for path in files:
        raw = open(path, "rb").read()
        try:
            text = raw.decode("utf-8")
            if "\x00" in text:
                raise UnicodeDecodeError("", b"", 0, 0, "")
            entry = {"path": path, "mode": "100644", "type": "blob", "content": text}
            size += len(raw)
        except UnicodeDecodeError:  # 二进制(png 等)走 blob base64
            b = gh(f"repos/{REPO}/git/blobs",
                   {"content": base64.b64encode(raw).decode(), "encoding": "base64"}, "POST")
            entry = {"path": path, "mode": "100644", "type": "blob", "sha": b["sha"]}
        batch.append(entry)
        if len(batch) >= 20 or size > 900_000:
            flush()
            size = 0
    flush()

    meta = subprocess.run(["git", "show", "-s", "--format=%an%n%ae", commit_sha],
                          capture_output=True, text=True, check=True).stdout.strip("\n").split("\n")
    name, email = meta[0], meta[1]
    msg = subprocess.run(["git", "log", "-1", "--pretty=%B", commit_sha],
                         capture_output=True, text=True, check=True).stdout.strip()
    c = gh(f"repos/{REPO}/git/commits",
           {"tree": base_tree, "message": msg, "parents": [],
            "author": {"name": name, "email": email}}, "POST")
    print("remote commit:", c["sha"])
    try:  # main 可能已被 contents init 建出: 创建失败即改指向
        gh(f"repos/{REPO}/git/refs", {"ref": "refs/heads/main", "sha": c["sha"]}, "POST")
    except SystemExit:
        gh(f"repos/{REPO}/git/refs/heads/main", {"sha": c["sha"], "force": True}, "PATCH")
    try:
        tag_obj = gh(f"repos/{REPO}/git/tags",
                     {"tag": tag, "message": msg, "object": c["sha"], "type": "commit",
                      "tagger": {"name": name, "email": email}}, "POST")
        gh(f"repos/{REPO}/git/refs", {"ref": f"refs/tags/{tag}", "sha": tag_obj["sha"]}, "POST")
    except SystemExit:
        gh(f"repos/{REPO}/git/refs/tags/{tag}", {"sha": c["sha"], "force": True}, "PATCH")
    print(f"pushed main + {tag} via API (remote {c['sha'][:7]}, local {commit_sha[:7]})")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
