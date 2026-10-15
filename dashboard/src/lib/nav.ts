import {
  Activity,
  BarChart3,
  ClipboardCheck,
  Code,
  Cog,
  FileText,
  Globe,
  House,
  Image,
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
  /** Home is the only page built in sub-project A. */
  ready: boolean;
};

export const NAV_ITEMS: readonly NavItem[] = [
  { id: "home", label: "Home", icon: House, ready: true },
  { id: "chat", label: "Chat", icon: MessageSquare, ready: false },
  { id: "voice", label: "Voice", icon: Mic, ready: false },
  { id: "tasks", label: "Tasks", icon: ClipboardCheck, ready: false },
  { id: "automation", label: "Automation", icon: Activity, ready: false },
  { id: "system", label: "System Control", icon: SlidersHorizontal, ready: false },
  { id: "apps", label: "Apps", icon: LayoutGrid, ready: false },
  { id: "research", label: "Research", icon: Telescope, ready: false },
  { id: "settings", label: "Settings", icon: Settings, ready: false },
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
    prompt: "Let's just talk. What's on your mind?",
  },
  {
    id: "search",
    label: "Web Search",
    sub: "Get Latest Info",
    icon: Globe,
    accent: "#14B8A6",
    prompt: "Search the web for me.",
  },
  {
    id: "code",
    label: "Code Assist",
    sub: "Programming",
    icon: Code,
    accent: "#2DD4BF",
    prompt: "Help me with some code.",
  },
  {
    id: "analyze",
    label: "Analyze",
    sub: "Files & Data",
    icon: FileText,
    accent: "#22C55E",
    prompt: "Analyze a file for me.",
  },
  {
    id: "create",
    label: "Create",
    sub: "Images & Content",
    icon: Image,
    accent: "#F43F5E",
    prompt: "Help me create something.",
  },
  {
    id: "automation",
    label: "Automation",
    sub: "Control Apps",
    icon: Cog,
    accent: "#F59E0B",
    prompt: "What workflows do you have saved?",
  },
  {
    id: "research",
    label: "Research",
    sub: "Deep Analysis",
    icon: BarChart3,
    accent: "#A855F7",
    prompt: "Do some deep research for me.",
  },
];

export type Suggestion = { id: string; label: string; prompt: string };

export const SUGGESTIONS: readonly Suggestion[] = [
  { id: "pdf", label: "Summarize this PDF", prompt: "Summarize this PDF for me." },
  { id: "news", label: "Latest AI news", prompt: "What's the latest AI news?" },
  { id: "chart", label: "Analyze this chart", prompt: "Analyze this chart." },
  { id: "vscode", label: "Open VS Code", prompt: "Open VS Code." },
  { id: "remind", label: "Set a reminder", prompt: "Set a reminder for me." },
  { id: "explain", label: "Explain this concept", prompt: "Explain this concept." },
];
