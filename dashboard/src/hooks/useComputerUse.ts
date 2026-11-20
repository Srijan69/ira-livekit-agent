import { useEffect, useRef, useState } from "react";
import type { IraEvent } from "@shared/types";
import { EMPTY_COMPUTER_USE, trackComputerUse } from "@/lib/computerUse";

export function useComputerUse({ subscribeEvents, connection }: {
  subscribeEvents: (listener: (event: IraEvent) => void) => () => void;
  connection: string;
}) {
  const [model, setModel] = useState(EMPTY_COMPUTER_USE);
  const connected = useRef(connection === "connected");
  connected.current = connection === "connected";
  useEffect(() => subscribeEvents((event) => {
    if (connected.current && event.type === "computer.observation") setModel((current) => trackComputerUse(current, event, Date.now()));
  }), [subscribeEvents]);
  useEffect(() => { if (connection !== "connected") setModel(EMPTY_COMPUTER_USE); }, [connection]);
  return connection === "connected" ? model : EMPTY_COMPUTER_USE;
}
