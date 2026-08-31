def configure_dns() -> None:
    import aiohttp
    import aiohttp.connector

    aiohttp.connector.DefaultResolver = aiohttp.ThreadedResolver

# This is just a small network adapter for multi-platform usage. Don't change pls