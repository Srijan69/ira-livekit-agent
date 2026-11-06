import blueDeck from "../../assets/themes/blue-deck.webp";
import bluePreview from "../../assets/themes/blue-preview.webp";
import silverDeck from "../../assets/themes/silver-deck.webp";
import silverPreview from "../../assets/themes/silver-preview.webp";
import redDeck from "../../assets/themes/red-deck.webp";
import redPreview from "../../assets/themes/red-preview.webp";
import pinkDeck from "../../assets/themes/pink-deck.webp";
import pinkPreview from "../../assets/themes/pink-preview.webp";
import goldDeck from "../../assets/themes/gold-deck.webp";
import goldPreview from "../../assets/themes/gold-preview.webp";
import greenDeck from "../../assets/themes/green-deck.webp";
import greenPreview from "../../assets/themes/green-preview.webp";
import type { ThemeId } from "./themes";

export const THEME_ASSETS: Record<ThemeId, { deck: string; preview: string }> =
  {
    blue: { deck: blueDeck, preview: bluePreview },
    silver: { deck: silverDeck, preview: silverPreview },
    red: { deck: redDeck, preview: redPreview },
    pink: { deck: pinkDeck, preview: pinkPreview },
    gold: { deck: goldDeck, preview: goldPreview },
    green: { deck: greenDeck, preview: greenPreview },
  };
