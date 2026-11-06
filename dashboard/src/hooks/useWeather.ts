import { useCallback, useEffect, useState } from "react";

import {
  DEFAULT_LOCATION,
  LOCATION_KEY,
  WEATHER_POLL_MS,
  forecastUrl,
  isWeather,
  isWeatherLocation,
  parseWeather,
  weatherCacheKey,
  type Weather,
  type WeatherLocation,
} from "@/lib/weather";

function stored(key: string): unknown {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}
function save(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Private storage can be unavailable. Live readings still work. */
  }
}
function cached(location: WeatherLocation): Weather | null {
  const value = stored(weatherCacheKey(location));
  return isWeather(value) ? { ...value, place: location.name } : null;
}

export function useWeather() {
  const [location, updateLocation] = useState<WeatherLocation>(() => {
    const value = stored(LOCATION_KEY);
    return isWeatherLocation(value) ? value : DEFAULT_LOCATION;
  });
  const [weather, setWeather] = useState<Weather | null>(() =>
    cached(location),
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;
    let interval: ReturnType<typeof setInterval> | undefined;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    setWeather(cached(location));
    setError(null);
    setLoading(true);

    const refresh = async () => {
      if (document.visibilityState === "hidden" || controller) return;
      const request = new AbortController();
      controller = request;
      timeout = setTimeout(() => request.abort(), 10_000);
      try {
        const response = await fetch(forecastUrl(location), {
          signal: request.signal,
        });
        if (!response.ok) throw new Error("Weather unavailable");
        const next = parseWeather(await response.json(), location);
        if (!active || request.signal.aborted) return;
        setWeather(next);
        setError(null);
        save(weatherCacheKey(location), next);
      } catch {
        if (active) setError("Weather update unavailable");
      } finally {
        clearTimeout(timeout);
        controller = null;
        if (active) setLoading(false);
      }
    };
    const visibility = () => {
      clearInterval(interval);
      if (document.visibilityState !== "hidden") {
        void refresh();
        interval = setInterval(() => {
          void refresh();
        }, WEATHER_POLL_MS);
      }
    };
    visibility();
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("online", visibility);
    return () => {
      active = false;
      controller?.abort();
      clearTimeout(timeout);
      clearInterval(interval);
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("online", visibility);
    };
  }, [location]);

  const setLocation = useCallback((next: WeatherLocation) => {
    if (!isWeatherLocation(next)) return;
    save(LOCATION_KEY, next);
    updateLocation(next);
  }, []);

  return { weather, location, loading, error, setLocation };
}
