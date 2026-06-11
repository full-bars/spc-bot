import asyncio
import aiohttp
from config import GEMINI_API_KEY


async def test_gemini():
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": "Hello world"}]}],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            print(f"Status: {resp.status}")
            text = await resp.text()
            print(f"Body: {text}")


if __name__ == "__main__":
    asyncio.run(test_gemini())
