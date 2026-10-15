import {
  ChevronRight,
  Cloud,
  CloudRain,
  CloudSun,
  CloudMoon,
  MapPin,
  Moon,
  Sun,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { Weather } from "@/lib/mock";

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
  }
}

/** Minutes since the reading, for the stale marker. */
function minutesOld(iso: string): number {
  const age = Date.now() - new Date(iso).getTime();
  return Math.max(0, Math.floor(age / 60_000));
}

export function WeatherCard({
  weather,
  delay = 0,
}: {
  weather: Weather | null;
  delay?: number;
}) {
  if (!weather) {
    return (
      <Panel delay={delay}>
        <div className="flex h-[118px] items-center justify-center">
          <p className="label-caps">Weather unavailable</p>
        </div>
      </Panel>
    );
  }

  const Icon = iconFor(weather);
  // Open-Meteo going down shouldn't look like a broken app, so a stale reading
  // is shown with its age rather than replaced by an empty card.
  const age = minutesOld(weather.at);
  const stale = age > 30;

  return (
    <Panel delay={delay}>
      <header className="flex items-center gap-2 px-4 pt-3.5 pb-1">
        <MapPin size={13} strokeWidth={1.75} className="shrink-0 text-cyan" />
        <h2 className="flex-1 truncate text-[13px] font-semibold text-text">
          {weather.place}
        </h2>
        {stale && <span className="label-caps !text-[9px]">{age}m ago</span>}
        <ChevronRight size={15} strokeWidth={1.75} className="text-mute" />
      </header>

      <div className="flex items-center gap-3 px-4 pt-1 pb-4">
        <Icon size={38} strokeWidth={1.25} className="shrink-0 text-cyan/90" />

        <div className="leading-none">
          <p className="flex items-start">
            <span className="tnum text-[34px] font-light tracking-tight text-text">
              {Math.round(weather.temperatureC)}
            </span>
            <span className="mt-1.5 text-[15px] font-light text-dim">&deg;C</span>
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
    </Panel>
  );
}
