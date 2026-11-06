import { describe, expect, it, vi } from "vitest";
import {
  DEFAULT_LOCATION,
  forecastUrl,
  parseWeather,
  searchLocations,
} from "./weather";

export const CURRENT = {
  time: 1792058400,
  temperature_2m: 28,
  apparent_temperature: 30,
  relative_humidity_2m: 64,
  wind_speed_10m: 12,
  weather_code: 2,
  is_day: 0,
};

describe("Open-Meteo weather", () => {
  it("requests current values in explicit units with UTC epoch timestamps", () => {
    const url = new URL(forecastUrl(DEFAULT_LOCATION));
    expect(url.origin).toBe("https://api.open-meteo.com");
    expect(url.searchParams.get("current")?.split(",")).toEqual([
      "temperature_2m",
      "apparent_temperature",
      "relative_humidity_2m",
      "wind_speed_10m",
      "weather_code",
      "is_day",
    ]);
    expect(url.searchParams.get("timeformat")).toBe("unixtime");
    expect(url.searchParams.get("wind_speed_unit")).toBe("kmh");
  });

  it.each([
    [0, "clear"],
    [1, "clear"],
    [2, "partly-cloudy"],
    [3, "cloudy"],
    [45, "fog"],
    [48, "fog"],
    [51, "rain"],
    [57, "rain"],
    [61, "rain"],
    [67, "rain"],
    [71, "snow"],
    [77, "snow"],
    [80, "rain"],
    [86, "snow"],
    [95, "storm"],
    [99, "storm"],
  ])("maps WMO %s to %s", (code, condition) => {
    expect(
      parseWeather(
        { current: { ...CURRENT, weather_code: code } },
        DEFAULT_LOCATION,
      ).condition,
    ).toBe(condition);
  });

  it("derives day/night from the provider and rejects incomplete readings", () => {
    expect(parseWeather({ current: CURRENT }, DEFAULT_LOCATION)).toMatchObject({
      temperatureC: 28,
      feelsLikeC: 30,
      humidity: 64,
      windKph: 12,
      isNight: true,
      at: new Date(CURRENT.time * 1000).toISOString(),
    });
    expect(
      parseWeather({ current: { ...CURRENT, is_day: 1 } }, DEFAULT_LOCATION)
        .isNight,
    ).toBe(false);
    expect(() =>
      parseWeather(
        { current: { ...CURRENT, temperature_2m: null } },
        DEFAULT_LOCATION,
      ),
    ).toThrow();
    expect(() =>
      parseWeather({ current: { ...CURRENT, is_day: 2 } }, DEFAULT_LOCATION),
    ).toThrow();
  });

  it("bounds city searches and ignores malformed results", async () => {
    const fetcher = vi.fn<typeof fetch>(
      async () =>
        ({
          ok: true,
          json: async () => ({
            results: [
              {
                name: "Delhi",
                country: "India",
                latitude: 28.61,
                longitude: 77.21,
              },
              { name: "Bad", latitude: 200, longitude: 1 },
            ],
          }),
        }) as Response,
    );
    expect(await searchLocations("Delhi", undefined, fetcher)).toEqual([
      { name: "Delhi, India", latitude: 28.61, longitude: 77.21 },
    ]);
    expect(
      new URL(String(fetcher.mock.calls[0][0])).searchParams.get("count"),
    ).toBe("5");
    expect(await searchLocations("x", undefined, fetcher)).toEqual([]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
