import {
  ChevronRight,
  Cloud,
  CloudRain,
  CloudSun,
  CloudMoon,
  CloudFog,
  Snowflake,
  MapPin,
  Moon,
  Sun,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  DEFAULT_LOCATION,
  searchLocations,
  type Weather,
  type WeatherLocation,
} from "@/lib/weather";

import { Panel } from "./Panel";

/** Day and night variants matter: a sun over "Partly Cloudy" at 7pm is wrong. */
function iconFor(weather: Weather): LucideIcon {
  switch (weather.condition) {
    case "clear":
      return weather.isNight ? Moon : Sun;
    case "partly-cloudy":
      return weather.isNight ? CloudMoon : CloudSun;
    case "cloudy":
      return Cloud;
    case "rain":
      return CloudRain;
    case "storm":
      return Zap;
    case "fog":
      return CloudFog;
    case "snow":
      return Snowflake;
  }
}

/** Minutes since the reading, for the stale marker. */
function minutesOld(iso: string): number {
  const age = Date.now() - new Date(iso).getTime();
  return Math.max(0, Math.floor(age / 60_000));
}

export function WeatherCard({
  weather,
  location = DEFAULT_LOCATION,
  onLocationChange,
  loading = false,
  error = null,
  delay = 0,
}: {
  weather: Weather | null;
  location?: WeatherLocation;
  onLocationChange?: (location: WeatherLocation) => void;
  loading?: boolean;
  error?: string | null;
  delay?: number;
}) {
  const [editing, setEditing] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<WeatherLocation[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const search = async (event: FormEvent) => {
    event.preventDefault();
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const timeout = setTimeout(() => controller.abort(), 10_000);
    setSearching(true);
    setSearchError(null);
    setResults([]);
    try {
      const places = await searchLocations(query, controller.signal);
      if (request.current !== controller || controller.signal.aborted) return;
      setResults(places);
      if (!places.length) setSearchError("No matching cities found.");
    } catch {
      if (request.current === controller)
        setSearchError("Location search unavailable. Try again.");
    } finally {
      clearTimeout(timeout);
      if (request.current === controller) setSearching(false);
    }
  };
  const Icon = weather ? iconFor(weather) : Cloud;
  // Open-Meteo going down shouldn't look like a broken app, so a stale reading
  // is shown with its age rather than replaced by an empty card.
  const age = weather ? minutesOld(weather.at) : 0;
  const stale = age > 30 || !!error;

  return (
    <Panel delay={delay}>
      <header className="flex items-center gap-2 px-4 pt-3.5 pb-1">
        <MapPin size={13} strokeWidth={1.75} className="shrink-0 text-cyan" />
        <h2 className="flex-1 truncate text-[13px] font-semibold text-text">
          {location.name}
        </h2>
        {weather && stale && (
          <span className="label-caps !text-[9px]" title={error ?? undefined}>
            {age}m ago
          </span>
        )}
        {onLocationChange && (
          <button
            type="button"
            aria-label="Change weather location"
            aria-expanded={editing}
            onClick={() => setEditing(!editing)}
            className="text-mute hover:text-cyan"
          >
            <ChevronRight size={15} strokeWidth={1.75} />
          </button>
        )}
      </header>

      {editing && (
        <div className="px-4 py-2">
          <form onSubmit={search} className="flex gap-2">
            <input
              aria-label="City name"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              minLength={2}
              maxLength={80}
              required
              placeholder="Search a city"
              className="min-w-0 flex-1 rounded border border-white/15 bg-black/20 px-2 py-1 text-[12px] text-text"
            />
            <button
              type="submit"
              disabled={searching}
              className="text-[11px] text-cyan disabled:opacity-50"
            >
              {searching ? "Searching…" : "Search"}
            </button>
          </form>
          {searchError && (
            <p role="status" className="mt-2 text-[11px] text-warn">
              {searchError}
            </p>
          )}
          <ul className="max-h-32 overflow-y-auto">
            {results.map((place) => (
              <li key={`${place.latitude},${place.longitude}`}>
                <button
                  type="button"
                  onClick={() => {
                    onLocationChange?.(place);
                    setEditing(false);
                  }}
                  className="w-full py-1.5 text-left text-[11px] text-dim hover:text-cyan"
                >
                  {place.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {weather ? (
        <div className="flex items-center gap-3 px-4 pt-1 pb-4">
          <Icon
            size={38}
            strokeWidth={1.25}
            className="shrink-0 text-cyan/90"
          />

          <div className="leading-none">
            <p className="flex items-start">
              <span className="tnum text-[34px] font-light tracking-tight text-text">
                {Math.round(weather.temperatureC)}
              </span>
              <span className="mt-1.5 text-[15px] font-light text-dim">
                &deg;C
              </span>
            </p>
            <p className="mt-1 text-[11px] text-dim">{weather.description}</p>
          </div>

          <dl className="ml-auto space-y-1 text-right text-[10.5px] leading-tight">
            <div>
              <dt className="inline text-mute">Feels like </dt>
              <dd className="tnum inline text-dim">
                {Math.round(weather.feelsLikeC)}&deg;C
              </dd>
            </div>
            <div>
              <dt className="inline text-mute">Humidity </dt>
              <dd className="tnum inline text-dim">{weather.humidity}%</dd>
            </div>
            <div>
              <dt className="inline text-mute">Wind </dt>
              <dd className="tnum inline text-dim">{weather.windKph} km/h</dd>
            </div>
          </dl>
        </div>
      ) : (
        <div className="flex h-[88px] items-center justify-center">
          <p className="label-caps">
            {loading ? "Loading weather…" : "Weather unavailable"}
          </p>
        </div>
      )}
    </Panel>
  );
}
