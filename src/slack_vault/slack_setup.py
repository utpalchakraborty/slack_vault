"""Slack setup preflight checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from slack_vault.config import Settings

_SENTINEL_FILE_ID = "F0000000000"


class SlackSetupBotClient(Protocol):
    """Slack Web API methods used by setup checks with the bot token."""

    def auth_test(self) -> Mapping[str, object]:
        """Return Slack auth.test response."""

    def api_test(self) -> Mapping[str, object]:
        """Return Slack api.test response."""

    def conversations_info(self, **kwargs: object) -> Mapping[str, object]:
        """Return Slack conversations.info response."""

    def conversations_history(self, **kwargs: object) -> Mapping[str, object]:
        """Return Slack conversations.history response."""

    def files_info(self, **kwargs: object) -> Mapping[str, object]:
        """Return Slack files.info response."""


class SlackSetupAppClient(Protocol):
    """Slack Web API methods used by setup checks with the app token."""

    def apps_connections_open(self, **kwargs: object) -> Mapping[str, object]:
        """Return Slack apps.connections.open response."""


class AddCheck(Protocol):
    """Callable used internally to append a check result."""

    def __call__(
        self,
        name: str,
        ok: bool,
        detail: str | None = None,
    ) -> None:
        """Append one check result."""


@dataclass(frozen=True)
class SlackSetupCheck:
    """One setup check result."""

    name: str
    ok: bool
    detail: str | None = None


@dataclass(frozen=True)
class SlackSetupCheckResult:
    """Slack setup preflight result."""

    checks: tuple[SlackSetupCheck, ...]

    @property
    def ok(self) -> bool:
        """Return whether every setup check passed."""

        return all(check.ok for check in self.checks)


def run_slack_setup_check(
    settings: Settings,
    *,
    bot_client: SlackSetupBotClient | None = None,
    app_client: SlackSetupAppClient | None = None,
) -> SlackSetupCheckResult:
    """Check that Slack settings and token permissions are usable."""

    checks: list[SlackSetupCheck] = []

    def add(name: str, ok: bool, detail: str | None = None) -> None:
        checks.append(SlackSetupCheck(name=name, ok=ok, detail=detail))

    slack = settings.slack
    add("SLACK_BOT_TOKEN configured", slack.bot_token is not None)
    add("SLACK_APP_TOKEN configured", slack.app_token is not None)
    add("SLACK_SIGNING_SECRET configured", slack.signing_secret is not None)
    add("SLACK_VAULT_TEAM_ID configured", slack.team_id is not None, slack.team_id)
    add(
        "SLACK_VAULT_INGESTION_CHANNEL_ID configured",
        slack.ingestion_channel_id is not None,
        slack.ingestion_channel_id,
    )
    add(
        "SLACK_VAULT_INGESTION_CHANNEL_NAME configured",
        slack.ingestion_channel_name is not None,
        slack.ingestion_channel_name,
    )
    add("SLACK_VAULT_ENTERPRISE_ID optional", True, slack.enterprise_id or "not set")

    if slack.bot_token is not None:
        bot = bot_client or cast(SlackSetupBotClient, WebClient(token=slack.bot_token))
        auth_team_id = _check_bot_auth(add, bot)
        _check_api_reachable(add, bot)
        if slack.team_id is not None and auth_team_id is not None:
            add(
                "configured team matches bot token",
                auth_team_id == slack.team_id,
                f"auth.team_id={auth_team_id} configured={slack.team_id}",
            )
        if slack.ingestion_channel_id is not None:
            _check_channel(add, bot, settings)
            _check_channel_history_scope(add, bot, slack.ingestion_channel_id)
        _check_files_read_scope(add, bot)

    if slack.app_token is not None:
        app = app_client or cast(SlackSetupAppClient, WebClient())
        _check_socket_mode(add, app, slack.app_token)

    return SlackSetupCheckResult(checks=tuple(checks))


def render_slack_setup_check(result: SlackSetupCheckResult) -> str:
    """Render Slack setup checks for CLI output."""

    lines = ["Slack Vault setup check"]
    for check in result.checks:
        status = "PASS" if check.ok else "FAIL"
        detail = "" if check.detail is None else f" - {check.detail}"
        lines.append(f"{status}: {check.name}{detail}")
    return "\n".join(lines)


def _check_bot_auth(
    add: AddCheck,
    bot: SlackSetupBotClient,
) -> str | None:
    try:
        auth = bot.auth_test()
    except SlackApiError as exc:
        add("bot auth.test", False, _slack_error_detail(exc))
        return None
    except Exception as exc:
        add("bot auth.test", False, _exception_detail(exc))
        return None

    ok = bool(auth.get("ok"))
    team_id = _optional_string(auth.get("team_id"))
    user_id = _optional_string(auth.get("user_id"))
    add("bot auth.test", ok, f"team={team_id} user={user_id}")
    return team_id


def _check_api_reachable(add: AddCheck, bot: SlackSetupBotClient) -> None:
    try:
        response = bot.api_test()
    except SlackApiError as exc:
        add("bot api.test", False, _slack_error_detail(exc))
        return
    except Exception as exc:
        add("bot api.test", False, _exception_detail(exc))
        return

    add("bot api.test", bool(response.get("ok")))


def _check_channel(
    add: AddCheck,
    bot: SlackSetupBotClient,
    settings: Settings,
) -> None:
    channel_id = settings.slack.ingestion_channel_id
    if channel_id is None:
        return

    try:
        response = bot.conversations_info(channel=channel_id)
    except SlackApiError as exc:
        add("channel conversations.info", False, _slack_error_detail(exc))
        return
    except Exception as exc:
        add("channel conversations.info", False, _exception_detail(exc))
        return

    channel = _mapping(response.get("channel"))
    if channel is None:
        add("channel conversations.info", False, "missing channel object")
        return

    name = _optional_string(channel.get("name")) or _optional_string(
        channel.get("name_normalized")
    )
    is_member = channel.get("is_member")
    add(
        "channel conversations.info",
        bool(response.get("ok")),
        f"name={name} is_member={is_member}",
    )
    if settings.slack.ingestion_channel_name is not None:
        add(
            "configured channel name matches",
            name == settings.slack.ingestion_channel_name,
            f"slack={name} configured={settings.slack.ingestion_channel_name}",
        )
    add(
        "bot is member of ingestion channel",
        is_member is True,
        f"is_member={is_member}",
    )


def _check_channel_history_scope(
    add: AddCheck,
    bot: SlackSetupBotClient,
    channel_id: str,
) -> None:
    try:
        response = bot.conversations_history(channel=channel_id, limit=1)
    except SlackApiError as exc:
        add("channel history scope", False, _slack_error_detail(exc))
        return
    except Exception as exc:
        add("channel history scope", False, _exception_detail(exc))
        return

    add("channel history scope", bool(response.get("ok")))


def _check_files_read_scope(add: AddCheck, bot: SlackSetupBotClient) -> None:
    try:
        response = bot.files_info(file=_SENTINEL_FILE_ID)
    except SlackApiError as exc:
        error = _slack_error(exc)
        if error in {"file_not_found", "file_deleted", "file_access_denied"}:
            add("files.info scope", True, error)
            return
        add("files.info scope", False, _slack_error_detail(exc))
        return
    except Exception as exc:
        add("files.info scope", False, _exception_detail(exc))
        return

    add(
        "files.info scope", bool(response.get("ok")), "sentinel file unexpectedly found"
    )


def _check_socket_mode(
    add: AddCheck,
    app: SlackSetupAppClient,
    app_token: str,
) -> None:
    try:
        response = app.apps_connections_open(app_token=app_token)
    except SlackApiError as exc:
        add("app token apps.connections.open", False, _slack_error_detail(exc))
        return
    except Exception as exc:
        add("app token apps.connections.open", False, _exception_detail(exc))
        return

    has_url = _optional_string(response.get("url")) is not None
    detail = "websocket url returned" if has_url else "no websocket url returned"
    add("app token apps.connections.open", bool(response.get("ok")) and has_url, detail)


def _slack_error(exc: SlackApiError) -> str:
    return str(exc.response.get("error"))


def _slack_error_detail(exc: SlackApiError) -> str:
    detail = f"error={_slack_error(exc)}"
    data = getattr(exc.response, "data", None)
    if isinstance(data, Mapping):
        needed = data.get("needed")
        provided = data.get("provided")
        if needed is not None:
            detail = f"{detail} needed={needed}"
        if provided is not None:
            detail = f"{detail} provided={provided}"
    return detail


def _exception_detail(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _optional_string(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    return None
