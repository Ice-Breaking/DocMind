"""账号管理 CLI：python -m docmind.manage_users <命令>

命令：
    list                       列出所有账号
    add <用户名> <密码>         新建账号
    reset <用户名> <密码>       重置密码
    del <用户名>                删除账号
    make-admin <用户名>         设为管理员（可访问 /admin 管理后台）
    drop-admin <用户名>         取消管理员

密码以 pbkdf2-sha256（20 万轮 + 随机盐）哈希存储于 data/chat.db。
"""
import sys

from docmind import store


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "list":
        users = store.list_users()
        if not users:
            print("（无账号）")
        for u in users:
            flag = " 👑管理员" if store.is_admin(u["username"]) else ""
            print(f"  {u['username']}{flag}")
        return 0
    if cmd == "add" and len(args) == 3:
        ok = store.create_user(args[1], args[2])
        print(f"已创建账号 {args[1]}" if ok else f"账号 {args[1]} 已存在")
        return 0 if ok else 1
    if cmd == "reset" and len(args) == 3:
        ok = store.set_password(args[1], args[2])
        print(f"已重置 {args[1]} 的密码" if ok else f"账号 {args[1]} 不存在")
        return 0 if ok else 1
    if cmd == "del" and len(args) == 2:
        ok = store.delete_user(args[1])
        print(f"已删除账号 {args[1]}" if ok else f"账号 {args[1]} 不存在")
        return 0 if ok else 1
    if cmd == "make-admin" and len(args) == 2:
        ok = store.set_admin(args[1], True)
        print(f"已设为管理员: {args[1]}" if ok else f"账号 {args[1]} 不存在")
        return 0 if ok else 1
    if cmd == "drop-admin" and len(args) == 2:
        ok = store.set_admin(args[1], False)
        print(f"已取消管理员: {args[1]}" if ok else f"账号 {args[1]} 不存在")
        return 0 if ok else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
