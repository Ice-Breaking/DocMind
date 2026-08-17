"""企业 LDAP 认证：配置 LDAP_URL + LDAP_USER_DN_TEMPLATE 后启用。

登录链位置：本地账号校验失败 → LDAP bind 校验 → 首登自动开通本地账号。
ldap3 未安装或配置不全时 authenticate 恒返回 False，不影响本地登录。
"""
import logging

from docmind import config

logger = logging.getLogger(__name__)


def authenticate(username: str, password: str) -> bool:
    """LDAP 简单绑定认证：用户 DN 模板 + 密码 bind 成功即通过"""
    if not (config.LDAP_URL and config.LDAP_USER_DN_TEMPLATE):
        return False
    if not username or not password:
        return False
    try:
        import ldap3
    except ImportError:
        logger.warning("ldap3 未安装，LDAP 登录不可用（pip install ldap3）")
        return False
    try:
        server = ldap3.Server(config.LDAP_URL, connect_timeout=5)
        dn = config.LDAP_USER_DN_TEMPLATE.format(username=username)
        conn = ldap3.Connection(server, user=dn, password=password,
                                auto_bind=True, receive_timeout=10)
        try:
            return True
        finally:
            conn.unbind()
    except Exception as e:  # noqa: BLE001 - LDAP 故障只降级为认证失败
        logger.info(f"LDAP 认证未通过（{username}）: {type(e).__name__}")
        return False
