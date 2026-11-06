export type WeatherLocation = {
  name: string;
  latitude: number;
  longitude: number;
};
export type Weather = {
  place: string;
  temperatureC: number;
  feelsLikeC: number;
  humidity: number;
  windKph: number;
  description: string;
  condition:
    "clear" | "partly-cloudy" | "cloudy" | "rain" | "storm" | "fog" | "snow";
  isNight: boolean;
  at: string;
};

export const DEFAULT_LOCATION: WeatherLocation = {
  name: "Hyderabad, India",
  latitude: 17.385,
  longitude: 78.4867,
};
export const LOCATION_KEY = "ira.weather.location.v1";
export const WEATHER_POLL_MS = 15 * 60_000;

const finite = (n: unknown): n is number =>
  typeof n === "number" && Number.isFinite(n);
export function isWeatherLocation(value: unknown): value is WeatherLocation {
  const v = value as Partial<WeatherLocation> | null;
  return (
    !!v &&
    typeof v.name === "string" &&
    v.name.trim().length > 0 &&
    v.name.length <= 160 &&
    finite(v.latitude) &&
    Math.abs(v.latitude) <= 90 &&
    finite(v.longitude) &&
    Math.abs(v.longitude) <= 180
  );
}
export const weatherCacheKey = (location: WeatherLocation) =>
  `ira.weather.v1:${location.latitude},${location.longitude}`;

/** Parameter names and WMO codes: https://open-meteo.com/en/docs */
export function forecastUrl(location: WeatherLocation): string {
  const params = new URLSearchParams({
    latitude: String(location.latitude),
    longitude: String(location.longitude),
    current:
      "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,is_day",
    temperature_unit: "celsius",
    wind_speed_unit: "kmh",
    timeformat: "unixtime",
    timezone: "UTC",
  });
  return `https://api.open-meteo.com/v1/forecast?${params}`;
}

function conditionFor(
  code: number,
): Pick<Weather, "condition" | "description"> {
  if (code === 0) return { condition: "clear", description: "Clear sky" };
  if (code === 1) return { condition: "clear", description: "Mainly clear" };
  if (code === 2)
    return { condition: "partly-cloudy", description: "Partly cloudy" };
  if (code === 3) return { condition: "cloudy", description: "Overcast" };
  if ([45, 48].includes(code))
    return {
      condition: "fog",
      description: code === 48 ? "Freezing fog" : "Fog",
    };
  if ([51, 53, 55, 56, 57].includes(code))
    return {
      condition: "rain",
      description: code >= 56 ? "Freezing drizzle" : "Drizzle",
    };
  if ([61, 63, 65, 66, 67].includes(code))
    return {
      condition: "rain",
      description: code >= 66 ? "Freezing rain" : "Rain",
    };
  if ([71, 73, 75, 77, 85, 86].includes(code))
    return {
      condition: "snow",
      description: code >= 85 ? "Snow showers" : "Snow",
    };
  if ([80, 81, 82].includes(code))
    return { condition: "rain", description: "Rain showers" };
  if ([95, 96, 99].includes(code))
    return {
      condition: "storm",
      description: code === 95 ? "Thunderstorm" : "Thunderstorm with hail",
    };
  throw new Error("Unknown weather condition");
}

export function parseWeather(
  payload: unknown,
  location: WeatherLocation,
): Weather {
  const c = (payload as { current?: Record<string, unknown> } | null)?.current;
  if (
    !c ||
    !finite(c.time) ||
    !finite(c.temperature_2m) ||
    !finite(c.apparent_temperature) ||
    !finite(c.relative_humidity_2m) ||
    c.relative_humidity_2m < 0 ||
    c.relative_humidity_2m > 100 ||
    !finite(c.wind_speed_10m) ||
    c.wind_speed_10m < 0 ||
    !finite(c.weather_code) ||
    (c.is_day !== 0 && c.is_day !== 1)
  )
    throw new Error("Incomplete weather reading");
  return {
    place: location.name,
    temperatureC: c.temperature_2m,
    feelsLikeC: c.apparent_temperature,
    humidity: c.relative_humidity_2m,
    windKph: c.wind_speed_10m,
    ...conditionFor(c.weather_code),
    isNight: c.is_day === 0,
    at: new Date(c.time * 1_000).toISOString(),
  };
}

export function isWeather(value: unknown): value is Weather {
  const v = value as Weather | null;
  return (
    !!v &&
    typeof v.place === "string" &&
    finite(v.temperatureC) &&
    finite(v.feelsLikeC) &&
    finite(v.humidity) &&
    v.humidity >= 0 &&
    v.humidity <= 100 &&
    finite(v.windKph) &&
    v.windKph >= 0 &&
    typeof v.description === "string" &&
    [
      "clear",
      "partly-cloudy",
      "cloudy",
      "rain",
      "storm",
      "fog",
      "snow",
    ].includes(v.condition) &&
    typeof v.isNight === "boolean" &&
    typeof v.at === "string" &&
    Number.isFinite(Date.parse(v.at))
  );
}

/** Search only on submit, with bounded input and five results. */
export async function searchLocations(
  query: string,
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<WeatherLocation[]> {
  const name = query.trim();
  if (name.length < 2 || name.length > 80) return [];
  const params = new URLSearchParams({
    name,
    count: "5",
    language: "en",
    format: "json",
  });
  const response = await fetcher(
    `https://geocoding-api.open-meteo.com/v1/search?${params}`,
    { signal },
  );
  if (!response.ok) throw new Error("Location search unavailable. Try again.");
  const payload = (await response.json()) as {
    results?: Array<Record<string, unknown>>;
  };
  if (!Array.isArray(payload.results)) return [];
  return payload.results
    .slice(0, 5)
    .map((item) => ({
      name: [item.name, item.admin1, item.country]
        .filter(
          (value, index, all) =>
            typeof value === "string" && value && all.indexOf(value) === index,
        )
        .join(", "),
      latitude: item.latitude,
      longitude: item.longitude,
    }))
    .filter(isWeatherLocation);
}
