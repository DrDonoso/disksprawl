import ipaddress
import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Names that public DNS cannot serve, so a DNS rebinding attack cannot use them: mDNS, the special-use
# and private-use suffixes, and names ICANN will never delegate. Any other host name has to be listed
# in ALLOWED_HOSTS.
LOCAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".lan",
    ".home",
    ".home.arpa",
    ".internal",
    ".intranet",
    ".private",
    ".corp",
    ".localdomain",
)
MIN_PASSWORD_LENGTH = 12
MIN_SECRET_LENGTH = 32


def normalize_hostname(value: str) -> str:
    """Lower-case host name without brackets or a trailing dot (`[::1]` → `::1`)."""
    host = value.strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    # Where the SQLite database and the generated session key live (the `data` volume).
    data_dir: Path = Path("data")
    # Every analyzed directory must be inside this folder, after resolving symbolic links.
    scan_root: Path = Path("/data")
    # The built web app served at `/` (absent in development, where Vite serves it).
    frontend_dir: Path = Path("frontend/dist")
    tz: str = "Etc/UTC"
    # Optional single password; empty keeps the web interface open to anyone who can reach it.
    auth_password: SecretStr = SecretStr("")
    # Key that signs session cookies; generated and stored in data_dir when empty.
    auth_secret: SecretStr = SecretStr("")
    auth_cookie_secure: bool = False
    session_hours: int = Field(default=168, ge=1, le=8760)
    # Extra host names (comma-separated) the app answers to, besides IP addresses and local names.
    allowed_hosts: str = ""
    image_tag: str = "development"
    build_date: str = ""

    @field_validator("tz")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        value = value.strip() or "Etc/UTC"
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("TZ must be an IANA time zone such as Europe/Madrid") from None
        return value

    @field_validator("auth_password")
    @classmethod
    def valid_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        if password and len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"AUTH_PASSWORD must have at least {MIN_PASSWORD_LENGTH} characters")
        return value

    @field_validator("auth_secret")
    @classmethod
    def valid_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret and len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"AUTH_SECRET must have at least {MIN_SECRET_LENGTH} characters")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def valid_hosts(cls, value: str) -> str:
        for raw in value.split(","):
            entry = raw.strip()
            if not entry or entry == "*":
                continue
            host = normalize_hostname(entry.removeprefix("."))
            if (
                not host
                or any(character in host for character in "/@ \t")
                or (":" in host and not is_ip_address(host))
            ):
                raise ValueError(
                    "ALLOWED_HOSTS must list host names (without scheme or port) separated by commas"
                )
        return value

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_password.get_secret_value())

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "disksprawl.db"

    @property
    def extra_hosts(self) -> tuple[str, ...]:
        hosts = []
        for raw in self.allowed_hosts.split(","):
            entry = raw.strip()
            if entry == "*":
                hosts.append(entry)
            elif entry.startswith("."):
                hosts.append("." + normalize_hostname(entry[1:]))
            elif entry:
                hosts.append(normalize_hostname(entry))
        return tuple(hosts)

    def host_allowed(self, hostname: str) -> bool:
        """Whether a request may name this host: blocks DNS rebinding to the app."""
        host = normalize_hostname(hostname)
        if not host:
            return False
        for entry in self.extra_hosts:
            if entry == "*" or entry == host:
                return True
            if entry.startswith(".") and (host == entry[1:] or host.endswith(entry)):
                return True
        if is_ip_address(host) or host == "localhost" or "." not in host:
            return True
        return host.endswith(LOCAL_SUFFIXES)

    def signing_key(self) -> bytes:
        """AUTH_SECRET, or a random key created once in data_dir (readable by this user only)."""
        configured = self.auth_secret.get_secret_value()
        if configured:
            return configured.encode()
        path = self.data_dir / "session.key"
        try:
            key = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            key = ""
        if len(key) < MIN_SECRET_LENGTH:
            key = secrets.token_hex(32)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(key + "\n")
        return key.encode()
