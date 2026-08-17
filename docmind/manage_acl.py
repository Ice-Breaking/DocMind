"""文档级 ACL 管理 CLI：python -m docmind.manage_acl <命令>

命令：
    list                        全部文档的限制状态与授权清单
    restrict <文档名>            标记为受限（仅授权用户可见）
    unrestrict <文档名>          恢复公开
    grant <用户> <文档名>        授权受限文档给指定用户
    revoke <用户> <文档名>       撤销授权
"""
import sys

from docmind import acl


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "list":
        for item in acl.list_acl():
            flag = "🔒 受限" if item["restricted"] else "🌐 公开"
            grants = f"（授权: {', '.join(item['grants'])}）" if item["grants"] else ""
            print(f"  {flag}  {item['doc']}{grants}")
        return 0
    if cmd in ("restrict", "unrestrict") and len(args) == 2:
        doc = args[1]
        if doc not in acl.all_docs():
            print(f"文档不存在于知识库: {doc}")
            return 1
        acl.set_restricted(doc, cmd == "restrict")
        print(f"已{'限制' if cmd == 'restrict' else '解除限制'}: {doc}")
        return 0
    if cmd == "grant" and len(args) == 3:
        acl.grant(args[1], args[2])
        print(f"已授权 {args[2]} → {args[1]}")
        return 0
    if cmd == "revoke" and len(args) == 3:
        acl.revoke(args[1], args[2])
        print(f"已撤销 {args[2]} → {args[1]}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
