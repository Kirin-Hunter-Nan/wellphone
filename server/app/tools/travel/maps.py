"""Apple Maps lookup adapter."""

from urllib.parse import urlencode

import httpx

from app.tools.travel.models import AppleMapsPlace


class AppleMapsSearchClient:
    def __init__(self, token: str | None, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=20)
        self._owns_client = client is None

    async def search(
        self, query: str, destination: str, language: str = "zh-CN"
    ) -> AppleMapsPlace:
        fallback_url = "https://maps.apple.com/?" + urlencode(
            {"q": f"{query} {destination}"}
        )
        if not self._token:
            return AppleMapsPlace(name=query, map_url=fallback_url, verified=False)
        response = await self._client.get(
            "https://maps-api.apple.com/v1/search",
            headers={"Authorization": f"Bearer {self._token}"},
            params={"q": f"{query} {destination}", "lang": language},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return AppleMapsPlace(name=query, map_url=fallback_url, verified=False)
        result = results[0]
        coordinate = result.get("coordinate") or {}
        latitude, longitude = coordinate.get("latitude"), coordinate.get("longitude")
        map_url = fallback_url
        if latitude is not None and longitude is not None:
            map_url = "https://maps.apple.com/?" + urlencode({
                "q": result.get("name") or query,
                "ll": f"{latitude},{longitude}",
            })
        return AppleMapsPlace(
            name=result.get("name") or query,
            formatted_address=result.get("formattedAddress"),
            latitude=latitude,
            longitude=longitude,
            map_url=map_url,
            verified=True,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
