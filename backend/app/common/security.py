"""密码哈希、会话令牌和常量时间比较工具。"""

import base64
import hashlib
import hmac
import secrets


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    """使用 scrypt 和随机盐生成不可逆密码摘要。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    return "scrypt${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    """校验明文密码与已存储 scrypt 摘要。"""
    try:
        algorithm, salt_text, digest_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_session_token() -> str:
    """生成高熵登录会话令牌。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """将会话令牌转换为数据库可存储摘要。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def secure_equals(left: str, right: str) -> bool:
    """以常量时间比较两个文本值。"""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
