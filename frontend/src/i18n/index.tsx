// Panel translations. Keys are the English source strings; each language file
// maps them to its translation. Missing keys fall back to English, so a new
// string never shows up blank. `{name}` placeholders are filled from `vars`.
//
// Languages: everything the Lymow app ships (en, de, fr, it, es, pl, sl, zh)
// plus the Nordic languages (sv, nb, da, fi, is).

import { createContext, useContext, useState, type ReactNode } from "react";
import { useHassLanguage } from "../hass";
import da from "./da.json";
import de from "./de.json";
import es from "./es.json";
import fi from "./fi.json";
import fr from "./fr.json";
import is from "./is.json";
import it from "./it.json";
import nb from "./nb.json";
import pl from "./pl.json";
import sl from "./sl.json";
import sv from "./sv.json";
import zh from "./zh.json";

export const LANGUAGES: { code: string; name: string }[] = [
  { code: "en", name: "English" },
  { code: "de", name: "Deutsch" },
  { code: "fr", name: "Français" },
  { code: "it", name: "Italiano" },
  { code: "es", name: "Español" },
  { code: "pl", name: "Polski" },
  { code: "sl", name: "Slovenščina" },
  { code: "zh", name: "简体中文" },
  { code: "sv", name: "Svenska" },
  { code: "nb", name: "Norsk bokmål" },
  { code: "da", name: "Dansk" },
  { code: "fi", name: "Suomi" },
  { code: "is", name: "Íslenska" },
];

const DICTS: Record<string, Record<string, string>> = { de, fr, it, es, pl, sl, zh, sv, nb, da, fi, is };

// BCP-47 locale for Intl date/number formatting.
const LOCALE: Record<string, string> = { zh: "zh-CN", nb: "nb-NO" };

/** Map a Home Assistant language tag (sv, nb, nn, no, zh-Hans, en-GB…) onto a supported code. */
export function resolveLanguage(tag: string | undefined): string {
  const t = (tag ?? "en").toLowerCase();
  if (DICTS[t]) return t;
  const base = t.split(/[-_]/)[0];
  if (base === "no" || base === "nn") return "nb";
  return DICTS[base] ? base : "en";
}

export type Vars = Record<string, string | number>;
export type T = (text: string, vars?: Vars) => string;

export function translate(lang: string, text: string, vars?: Vars): string {
  let s = DICTS[lang]?.[text] ?? text;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  return s;
}

interface I18n {
  t: T;
  lang: string;
  locale: string;
  /** "auto" follows Home Assistant's language. */
  choice: string;
  setChoice(c: string): void;
  haLanguage: string;
}

const I18nContext = createContext<I18n | null>(null);
const STORAGE_KEY = "lymow_language";

export function I18nProvider({ children }: { children: ReactNode }) {
  const haTag = useHassLanguage();
  const [choice, setChoiceState] = useState<string>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) ?? "auto";
    } catch {
      return "auto"; // storage blocked: follow Home Assistant
    }
  });
  const haLanguage = resolveLanguage(haTag);
  const lang = choice === "auto" ? haLanguage : resolveLanguage(choice);
  const value: I18n = {
    lang,
    locale: LOCALE[lang] ?? lang,
    t: (text, vars) => translate(lang, text, vars),
    choice,
    setChoice(c) {
      try {
        localStorage.setItem(STORAGE_KEY, c);
      } catch {
        // storage blocked or full: keep the choice for this session only
      }
      setChoiceState(c);
    },
    haLanguage,
  };
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const v = useContext(I18nContext);
  if (!v) throw new Error("I18nProvider missing");
  return v;
}

export function useT(): T {
  return useI18n().t;
}
