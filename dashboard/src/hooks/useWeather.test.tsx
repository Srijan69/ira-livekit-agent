import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_LOCATION, weatherCacheKey } from "@/lib/weather";
import { useWeather } from "./useWeather";

const current = {
  time: 1792058400,
  temperature_2m: 28,
  apparent_temperature: 30,
  relative_humidity_2m: 64,
  wind_speed_10m: 12,
  weather_code: 2,
  is_day: 0,
};
const response = () =>
  ({ ok: true, json: async () => ({ current }) }) as Response;

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useWeather", () => {
  it("loads Hyderabad and retains its cached reading when the next refresh fails", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response())
      .mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useWeather());
    expect(result.current.loading).toBe(true);
    await act(async () => {});
    expect(result.current.weather?.temperatureC).toBe(28);
    expect(
      localStorage.getItem(weatherCacheKey(DEFAULT_LOCATION)),
    ).not.toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15 * 60_000);
    });
    expect(result.current.weather?.temperatureC).toBe(28);
    expect(result.current.error).toBeTruthy();
  });

  it("pauses while hidden and refreshes when visible again", async () => {
    const fetcher = vi.fn().mockResolvedValue(response());
    vi.stubGlobal("fetch", fetcher);
    renderHook(() => useWeather());
    await act(async () => {});
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45 * 60_000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("never displays another city's cache during a location change", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(response())
        .mockRejectedValue(new Error("offline")),
    );
    const { result } = renderHook(() => useWeather());
    await act(async () => {});
    await act(async () => {
      result.current.setLocation({
        name: "Delhi, India",
        latitude: 28.61,
        longitude: 77.21,
      });
    });
    expect(result.current.location.name).toBe("Delhi, India");
    expect(result.current.weather).toBeNull();
    expect(result.current.error).toBeTruthy();
    await act(async () => {
      result.current.setLocation(DEFAULT_LOCATION);
    });
    expect(result.current.weather?.place).toBe("Hyderabad, India");
  });

  it("ignores corrupt storage and shows unavailable without a good reading", async () => {
    localStorage.setItem(
      weatherCacheKey(DEFAULT_LOCATION),
      '{"temperatureC":"invented"}',
    );
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { result } = renderHook(() => useWeather());
    await act(async () => {});
    expect(result.current.weather).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeTruthy();
  });
});
