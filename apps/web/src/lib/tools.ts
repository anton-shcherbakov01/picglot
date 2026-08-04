import type { Locale } from "./i18n";

/**
 * Tool display names.
 *
 * Deliberately *not* in a `"use client"` module: the header calls this from the
 * browser, but the home page, the tool page and the footer are server
 * components and call it during render. A function exported from a client
 * module cannot be invoked on the server — Next throws rather than falling
 * back — so it lives here, where both sides can reach it.
 *
 * The names stay inline rather than in every locale dictionary because they are
 * short and identical in shape; only `en` and `ru` differ in practice, and an
 * unknown slug degrades to the slug itself instead of throwing.
 */
const TOOL_TITLES: Record<string, { en: string; ru: string }> = {
  "image-translator": { en: "Image translator", ru: "Переводчик изображений" },
  "translate-photo": { en: "Photo translator", ru: "Переводчик фотографий" },
  "screenshot-translator": {
    en: "Screenshot translator",
    ru: "Переводчик скриншотов",
  },
  "image-to-text": { en: "Image to text", ru: "Изображение в текст" },
  "jpg-to-word": { en: "Photo to Word", ru: "Фото в Word" },
  "image-to-excel": { en: "Image to Excel", ru: "Изображение в Excel" },
  "handwriting-to-text": { en: "Handwriting to text", ru: "Рукописный текст" },
  "pdf-translator": { en: "PDF translator", ru: "Переводчик PDF" },
  "pdf-ocr": { en: "PDF OCR", ru: "OCR для PDF" },
  "document-scanner": { en: "Document scanner", ru: "Сканер документов" },
  "receipt-scanner": { en: "Receipt scanner", ru: "Сканер чеков" },
  "invoice-ocr": { en: "Invoice OCR", ru: "Распознавание счетов" },
  batch: { en: "Batch processing", ru: "Пакетная обработка" },
};

export function toolTitle(slug: string, locale: Locale): string {
  const entry = TOOL_TITLES[slug];
  if (!entry) return slug;
  return locale === "ru" ? entry.ru : entry.en;
}
