"""Discord gateway geolocation utilities."""

import asyncio
import logging
import socket
from typing import Optional

import aiohttp

logger = logging.getLogger("spc_bot")


async def get_discord_gateway_url(bot) -> Optional[str]:
    """Get the Discord gateway URL the bot is connected to."""
    try:
        # Discord.py stores gateway info in the bot's websocket connection
        if bot.ws and hasattr(bot.ws, 'socket') and bot.ws.socket:
            # Get the remote address from the socket
            peername = bot.ws.socket.getpeername()
            if peername:
                return peername[0]  # Return IP address
    except Exception as e:
        logger.debug(f"Could not get gateway socket info: {e}")

    # Fallback: try to query Discord's gateway endpoint
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://discord.com/api/v10/gateway', timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    url = data.get('url', '').replace('wss://', '').replace('/', '')
                    return url
    except Exception as e:
        logger.debug(f"Could not query Discord gateway endpoint: {e}")

    return None


async def resolve_gateway_ip(gateway_url: Optional[str]) -> Optional[str]:
    """Resolve gateway URL to IP address."""
    if not gateway_url:
        return None

    try:
        # If it's already an IP address, return it
        try:
            socket.inet_aton(gateway_url)
            return gateway_url
        except socket.error:
            pass

        # Otherwise, resolve the hostname
        loop = asyncio.get_event_loop()
        ip = await loop.getaddrinfo(gateway_url, None)
        if ip:
            return ip[0][4][0]  # Return first IPv4 address
    except Exception as e:
        logger.debug(f"Could not resolve gateway IP: {e}")

    return None


async def geolocate_ip(ip: Optional[str]) -> Optional[str]:
    """Get geolocation for an IP address."""
    if not ip:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            # Using ip-api.com free tier (rate limited but no key required)
            async with session.get(
                f'http://ip-api.com/json/{ip}?fields=city,regionName,country',
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('status') == 'success':
                        city = data.get('city', '')
                        region = data.get('regionName', '')
                        country = data.get('country', '')

                        # Format the location string
                        parts = []
                        if city:
                            parts.append(city)
                        if region and region != city:
                            parts.append(region)
                        if country:
                            parts.append(country)

                        return ', '.join(parts) if parts else None
    except asyncio.TimeoutError:
        logger.debug("Gateway geolocation lookup timed out")
    except Exception as e:
        logger.debug(f"Could not geolocate gateway IP: {e}")

    return None


async def update_gateway_info(bot) -> None:
    """Update bot state with current Discord gateway information."""
    try:
        # Get gateway URL (returns IP from socket if available)
        gateway_url = await get_discord_gateway_url(bot)
        if gateway_url:
            bot.state.discord_gateway_url = gateway_url

        # Resolve to IP if needed
        if gateway_url and not bot.state.discord_gateway_ip:
            ip = await resolve_gateway_ip(gateway_url)
            if ip:
                bot.state.discord_gateway_ip = ip

        # Geolocate the IP
        if bot.state.discord_gateway_ip and not bot.state.discord_gateway_location:
            location = await geolocate_ip(bot.state.discord_gateway_ip)
            if location:
                bot.state.discord_gateway_location = location
    except Exception as e:
        logger.debug(f"Error updating gateway info: {e}")
