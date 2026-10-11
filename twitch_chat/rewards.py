import json
import urllib.error
import urllib.parse
import urllib.request


def fetch_rewards(client_id, owner_id, token):
    if not all((client_id, owner_id, token)):
        raise ValueError("Authorize your Twitch broadcaster account first.")
    query = urllib.parse.urlencode({"broadcaster_id": owner_id, "only_manageable_rewards": "false"})
    request = urllib.request.Request(
        "https://api.twitch.tv/helix/channel_points/custom_rewards?" + query,
        headers={"Client-Id": client_id, "Authorization": "Bearer " + token},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            rewards = json.load(response).get("data", [])
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ValueError("Broadcaster authorization expired or lacks reward permissions. Reauthorize the broadcaster account.") from None
        if exc.code == 403:
            raise ValueError("Twitch denied access. Check broadcaster authorization and Channel Points eligibility.") from None
        raise ValueError(f"Twitch returned HTTP {exc.code}. Try again later.") from None
    except (OSError, ValueError):
        raise ValueError("Could not load Twitch rewards. Check your connection and try again.") from None
    return sorted(rewards, key=lambda item: str(item.get("title", "")).casefold())
