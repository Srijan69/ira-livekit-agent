import {
  Activity,
  BarChart3,
  ClipboardCheck,
  Code,
  Cog,
  FileText,
  Globe,
  House,
  PenLine,
  LayoutGrid,
  MessageSquare,
  Mic,
  Settings,
  SlidersHorizontal,
  Telescope,
  type LucideIcon,
} from "lucide-react";

/**
 * Navigation, quick actions and suggestion chips.
 *
 * Real configuration, not placeholder data. Each action and chip already
 * carries the `prompt` string it will send to IRA over the `lk.chat` topic --
 * so wiring them up in sub-project B is a change of one handler, not of this
 * file.
 */

export type NavId =
  | "home"
  | "chat"
  | "voice"
  | "tasks"
  | "automation"
  | "system"
  | "apps"
  | "research"
  | "settings";

export type NavItem = {
  id: NavId;
  label: string;
  icon: LucideIcon;
  /** Whether this destination has an implemented screen. */
  ready: boolean;
};

export const NAV_ITEMS: readonly NavItem[] = [
  { id: "home", label: "Home", icon: House, ready: true },
  { id: "chat", label: "Chat", icon: MessageSquare, ready: false },
  { id: "voice", label: "Voice", icon: Mic, ready: false },
  { id: "tasks", label: "Tasks", icon: ClipboardCheck, ready: false },
  { id: "automation", label: "Automation", icon: Activity, ready: true },
  { id: "system", label: "System Control", icon: SlidersHorizontal, ready: false },
  { id: "apps", label: "Apps", icon: LayoutGrid, ready: false },
  { id: "research", label: "Research", icon: Telescope, ready: false },
  { id: "settings", label: "Settings", icon: Settings, ready: true },
];

export type QuickAction = {
  id: string;
  label: string;
  sub: string;
  icon: LucideIcon;
  /** Accent for the icon tile and the card's hover glow. */
  accent: string;
  /** Sent verbatim to IRA. */
  prompt: string;
};

export const QUICK_ACTIONS: readonly QuickAction[] = [
  {
    id: "chat",
    label: "Chat",
    sub: "General AI",
    icon: MessageSquare,
    accent: "#3B82F6",
    prompt: "I'd like to talk. Ask me one focused question about what's on my mind, then help with my answer.",
  },
  {
    id: "search",
    label: "Web Search",
    sub: "Get Latest Info",
    icon: Globe,
    accent: "#14B8A6",
    prompt: "Help me find information on the web. Ask me one focused question about what I want to find, then search based on my answer.",
  },
  {
    id: "code",
    label: "Code Assist",
    sub: "Programming",
    icon: Code,
    accent: "#2DD4BF",
    prompt: "Help me with programming. Ask me one focused question about the code or problem I'm working on, then help based on my answer.",
  },
  {
    id: "analyze",
    label: "Analyze",
    sub: "Text & Data",
    icon: FileText,
    accent: "#22C55E",
    prompt: "Help me analyze text or data. Ask me one focused question about what I want to understand, then work from details or text I paste here.",
  },
  {
    id: "create",
    label: "Create",
    sub: "Writing & Ideas",
    icon: PenLine,
    accent: "#F43F5E",
    prompt: "Help me create written content or develop an idea. Ask me one focused question about what I want to make, then help based on my answer.",
  },
  {
    id: "automation",
    label: "Automation",
    sub: "Control Apps",
    icon: Cog,
    accent: "#F59E0B",
    prompt: "Help me automate a task. Ask me one focused question about what I want done, then use your available app controls or saved workflows to help.",
  },
  {
    id: "research",
    label: "Research",
    sub: "Deep Analysis",
    icon: BarChart3,
    accent: "#A855F7",
    prompt: "Help me research a topic. Ask me one focused question about the topic or decision I'm exploring, then research based on my answer.",
  },
];

export type Suggestion = { id: string; label: string; prompt: string };

export const SUGGESTIONS: readonly Suggestion[] = [
  { id: "text", label: "Summarize text", prompt: "Help me summarize text. Ask me to paste the passage I want summarized." },
  { id: "news", label: "Latest AI news", prompt: "What's the latest AI news?" },
  { id: "data", label: "Analyze data", prompt: "Help me analyze data. Ask me one focused question about the figures or data I can paste here." },
  { id: "vscode", label: "Open VS Code", prompt: "Open VS Code." },
  { id: "task", label: "Add a task", prompt: "Help me add a task to my list. Ask me one focused question about what I need to do." },
  { id: "explain", label: "Explain a concept", prompt: "Help me understand a concept. Ask me one focused question about what I want explained." },
];
