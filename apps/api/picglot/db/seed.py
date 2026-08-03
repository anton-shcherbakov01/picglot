"""Seed data: plans, feature flags, SEO content, FAQ, blog posts and demo accounts.

Baseline data (plans, flags, content) is safe to load anywhere. Demo accounts
are gated behind ``SEED_ENABLED`` and are refused in production by the config
validator, so a production deploy can never ship a known password.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from picglot.core.config import settings
from picglot.core.logging import get_logger
from picglot.db.models import BlogPost, FeatureFlag, Plan, SeoPage, User
from picglot.db.session import session_scope
from picglot.domain import languages as language_table
from picglot.domain import plans as plan_catalog
from picglot.domain import tools as tool_catalog
from picglot.domain.enums import AdminRole, UserStatus

log = get_logger(__name__)

#: Language pairs that get their own landing page. Chosen for real demand, and
#: each one gets genuinely distinct copy (script notes, examples, FAQ).
SEO_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("ru", "en", "ru"),
    ("ru", "zh-Hans", "ru"),
    ("ru", "ja", "ru"),
    ("ru", "ko", "ru"),
    ("ru", "de", "ru"),
    ("ru", "tr", "ru"),
    ("ru", "ar", "ru"),
    ("en", "es", "en"),
    ("en", "zh-Hans", "en"),
    ("en", "ja", "en"),
    ("en", "de", "en"),
    ("en", "fr", "en"),
    ("en", "ru", "en"),
    ("en", "ar", "en"),
    ("en", "ko", "en"),
    ("es", "en", "es"),
    ("de", "en", "de"),
    ("fr", "en", "fr"),
    ("pt", "en", "pt"),
    ("tr", "en", "tr"),
    ("pl", "en", "pl"),
    ("uk", "en", "uk"),
    ("id", "en", "id"),
)


def run_seed(*, baseline_only: bool = False, force: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        result = {
            "plans": seed_plans(session, force=force),
            "feature_flags": seed_feature_flags(session, force=force),
            "seo_pages": seed_seo_pages(session, force=force),
            "blog_posts": seed_blog(session, force=force),
        }
        if not baseline_only and settings.seed_enabled:
            result["accounts"] = seed_accounts(session)
        else:
            result["accounts"] = {"skipped": True}
    log.info("seed.completed", **{key: str(value)[:80] for key, value in result.items()})
    return result


# --------------------------------------------------------------------------- #
# Baseline
# --------------------------------------------------------------------------- #
def seed_plans(session: Session, *, force: bool = False) -> int:
    created = 0
    for spec in plan_catalog.plan_specs():
        row = session.execute(select(Plan).where(Plan.code == spec.code)).scalar_one_or_none()
        if row is not None and not force:
            continue
        if row is None:
            row = Plan(code=spec.code)
            session.add(row)
            created += 1
        row.name = spec.name
        row.monthly_credits = spec.monthly_credits
        row.price_usd_cents = spec.price_usd_cents
        row.price_rub_kopecks = spec.price_rub_kopecks
        row.max_upload_bytes = spec.max_upload_bytes
        row.max_pdf_pages = spec.max_pdf_pages
        row.max_batch_files = spec.max_batch_files
        row.retention_hours = spec.retention_hours
        row.queue_priority = spec.priority
        row.export_formats = [str(item) for item in spec.export_formats]
        row.features = list(spec.features)
        row.is_public = spec.is_public
        row.external_ids = {
            "stripe_price": {
                "pro": settings.stripe_price_pro_monthly,
                "business": settings.stripe_price_business_monthly,
            }.get(spec.code, "")
        }
    session.flush()
    return created


FLAGS: tuple[tuple[str, str, bool], ...] = (
    ("advanced_inpaint", "GPU-backed background repair for complex photos", False),
    ("llm_ocr_fallback", "Use a multimodal model when classical OCR is unsure", False),
    ("vertical_cjk_render", "Render vertical Japanese and Chinese text", True),
    ("batch_folder_upload", "Allow dropping a whole folder into batch", True),
    ("share_watermark", "Offer a watermark option on shared results", True),
    ("local_only_mode", "Expose the local-only processing switch to users", False),
    ("new_editor_toolbar", "Redesigned editor toolbar", False),
    ("pdfa_export", "Offer PDF/A archival export where Ghostscript is present", True),
)


def seed_feature_flags(session: Session, *, force: bool = False) -> int:
    created = 0
    for key, description, enabled in FLAGS:
        row = session.get(FeatureFlag, key)
        if row is not None and not force:
            continue
        if row is None:
            row = FeatureFlag(key=key)
            session.add(row)
            created += 1
        row.description = description
        row.enabled = enabled
        row.rollout_percent = 100 if enabled else 0
    session.flush()
    return created


# --------------------------------------------------------------------------- #
# SEO content
# --------------------------------------------------------------------------- #
def _tool_copy(tool_slug: str, locale: str) -> dict[str, Any]:
    """Per-tool copy. Deliberately specific — no interchangeable filler."""
    en = {
        "image-translator": {
            "h1": "Translate text in images online",
            "description": (
                "Upload a photo, screenshot or scan and get it back with the text "
                "translated in place, keeping the original layout, colours and fonts."
            ),
            "intro": (
                "PicGlot reads every block of text in your image, translates it, "
                "removes the original words from the background and draws the translation "
                "where the old text used to be. You can correct any block before you "
                "download the result."
            ),
        },
        "translate-photo": {
            "h1": "Translate a photo from your phone",
            "description": (
                "Point your camera at a sign, menu or document and get a translated "
                "photo back. Perspective, shadows and rotation are corrected first."
            ),
            "intro": (
                "Photos taken by hand are rarely flat or evenly lit. Before recognising "
                "anything we straighten the page, remove shadows and fix the angle, which "
                "is usually the difference between readable text and noise."
            ),
        },
        "screenshot-translator": {
            "h1": "Translate screenshots instantly",
            "description": (
                "Paste a screenshot with Ctrl+V and get the interface translated while "
                "buttons and labels keep their size and position."
            ),
            "intro": (
                "Interface text is short, dense and often tiny. This mode keeps each "
                "label as its own block instead of merging them into paragraphs, so "
                "buttons stay buttons."
            ),
        },
        "image-to-text": {
            "h1": "Extract text from an image",
            "description": (
                "Get clean, copyable text from photos, screenshots and scans, with "
                "paragraphs, lists and tables preserved."
            ),
            "intro": (
                "Recognition returns each block with its position and a confidence score. "
                "Anything the engine was unsure about is highlighted so you can check it "
                "rather than discovering the error later."
            ),
        },
        "jpg-to-word": {
            "h1": "Convert a photo to an editable Word document",
            "description": (
                "Turn JPG, PNG, HEIC, TIFF or PDF pages into a .docx file with headings, "
                "paragraphs, lists and tables you can edit."
            ),
            "intro": (
                "We rebuild the document structure — not just the words. Headings become "
                "headings, lists become lists and tables become real Word tables."
            ),
        },
        "image-to-excel": {
            "h1": "Convert a photo of a table to Excel",
            "description": (
                "Turn a photographed or scanned table into a real .xlsx file with each "
                "value in its own cell and numbers stored as numbers."
            ),
            "intro": (
                "Ruled tables are read from their grid lines; tables without lines are "
                "reconstructed from the position of each value. When the structure is "
                "ambiguous we say so instead of guessing silently."
            ),
        },
        "handwriting-to-text": {
            "h1": "Convert handwriting to typed text",
            "description": (
                "Turn legible handwritten notes into editable text, with uncertain words "
                "marked so you can check them."
            ),
            "intro": (
                "Handwriting recognition is genuinely harder than print. Clear, separated "
                "writing works well; hurried cursive will need corrections. Every uncertain "
                "word is highlighted rather than presented as fact."
            ),
        },
        "pdf-translator": {
            "h1": "Translate a PDF and keep the layout",
            "description": (
                "Translate text and scanned PDFs page by page, keeping page size, images "
                "and tables. Download translated, bilingual or searchable PDFs."
            ),
            "intro": (
                "If a page already has a text layer we use it directly, which is both "
                "faster and more accurate than re-reading the image. Scanned pages go "
                "through full recognition."
            ),
        },
        "pdf-ocr": {
            "h1": "Make a scanned PDF searchable",
            "description": (
                "Add an invisible text layer to a scanned PDF so it can be searched, "
                "copied and indexed — the page looks exactly the same."
            ),
            "intro": (
                "The recognised text is placed invisibly on top of the original image at "
                "the right coordinates. Nothing about the appearance changes; the file "
                "simply becomes searchable."
            ),
        },
        "document-scanner": {
            "h1": "Scan documents with your camera",
            "description": (
                "Turn photos of documents into clean, straightened pages and combine them "
                "into one PDF."
            ),
            "intro": (
                "Edges are detected automatically, perspective is corrected and shadows "
                "are flattened. You can reorder, rotate and remove pages before exporting."
            ),
        },
        "receipt-scanner": {
            "h1": "Extract data from receipts",
            "description": (
                "Pull the merchant, date, totals, tax and line items out of a receipt "
                "into JSON, CSV or Excel."
            ),
            "intro": (
                "Fields that are not present on the receipt come back as null. We never "
                "invent a value to fill a gap — a missing tax line stays missing."
            ),
        },
        "invoice-ocr": {
            "h1": "Extract data from invoices",
            "description": (
                "Read invoice numbers, dates, totals, tax and line items into structured "
                "data you can import."
            ),
            "intro": (
                "Every extracted field carries its own confidence score and a link back "
                "to the region of the page it came from, so you can verify it in one click."
            ),
        },
        "batch": {
            "h1": "Process many files at once",
            "description": (
                "Upload up to 100 images or PDFs with one set of settings and download "
                "everything as a ZIP with a per-file report."
            ),
            "intro": (
                "Each file is processed independently, so one bad scan does not fail the "
                "batch. Failed files are listed in the report and can be retried on their own."
            ),
        },
    }
    ru = {
        "image-translator": {
            "h1": "Перевод текста на изображении онлайн",
            "description": (
                "Загрузите фотографию, скриншот или скан и получите изображение с "
                "переведённым текстом на прежних местах, с сохранением вёрстки и цветов."
            ),
            "intro": (
                "Сервис распознаёт каждый блок текста, переводит его, убирает исходные "
                "слова с фона и наносит перевод туда, где был оригинал. Любой блок можно "
                "исправить вручную перед скачиванием."
            ),
        },
        "translate-photo": {
            "h1": "Перевод фотографии с телефона",
            "description": (
                "Сфотографируйте вывеску, меню или документ и получите переведённое фото. "
                "Перспектива, тени и поворот исправляются автоматически."
            ),
            "intro": (
                "Снимки с рук почти никогда не бывают ровными. Сначала мы выравниваем "
                "страницу, убираем тени и исправляем угол — обычно именно это отличает "
                "читаемый текст от набора символов."
            ),
        },
        "screenshot-translator": {
            "h1": "Перевод скриншотов",
            "description": (
                "Вставьте скриншот через Ctrl+V и получите переведённый интерфейс: кнопки "
                "и подписи сохраняют размер и положение."
            ),
            "intro": (
                "Текст интерфейса короткий и плотный. В этом режиме каждая подпись "
                "остаётся отдельным блоком и не сливается в абзац."
            ),
        },
        "image-to-text": {
            "h1": "Извлечь текст из изображения",
            "description": (
                "Получите чистый текст из фотографий, скриншотов и сканов с сохранением "
                "абзацев, списков и таблиц."
            ),
            "intro": (
                "Каждый блок возвращается с координатами и оценкой уверенности. Сомнительные "
                "места подсвечиваются, чтобы вы проверили их сразу, а не потом."
            ),
        },
        "jpg-to-word": {
            "h1": "Фото в редактируемый Word",
            "description": (
                "Преобразуйте JPG, PNG, HEIC, TIFF или PDF в файл .docx с заголовками, "
                "абзацами, списками и таблицами."
            ),
            "intro": (
                "Мы восстанавливаем структуру документа, а не только слова: заголовки "
                "остаются заголовками, а таблицы становятся настоящими таблицами Word."
            ),
        },
        "image-to-excel": {
            "h1": "Фото таблицы в Excel",
            "description": (
                "Превратите снятую или отсканированную таблицу в настоящий файл .xlsx: "
                "каждое значение в своей ячейке, числа остаются числами."
            ),
            "intro": (
                "Таблицы с линиями читаются по сетке, таблицы без линий восстанавливаются "
                "по расположению значений. Если структура неоднозначна, мы честно об этом "
                "предупреждаем."
            ),
        },
        "handwriting-to-text": {
            "h1": "Рукописный текст в печатный",
            "description": (
                "Переведите разборчивые рукописные заметки в редактируемый текст с "
                "пометками там, где распознавание не уверено."
            ),
            "intro": (
                "Распознавание почерка объективно сложнее печати. Разборчивые записи "
                "распознаются хорошо, беглый курсив потребует правок. Неуверенные слова "
                "помечаются, а не выдаются за факт."
            ),
        },
        "pdf-translator": {
            "h1": "Перевод PDF с сохранением вёрстки",
            "description": (
                "Переводите текстовые и сканированные PDF постранично с сохранением "
                "размера страниц, изображений и таблиц."
            ),
            "intro": (
                "Если в странице уже есть текстовый слой, мы используем его напрямую — это "
                "быстрее и точнее повторного распознавания. Сканы проходят полный OCR."
            ),
        },
        "pdf-ocr": {
            "h1": "Сделать сканированный PDF searchable",
            "description": (
                "Добавьте невидимый текстовый слой в сканированный PDF: файл станет "
                "искать и копировать, внешний вид не изменится."
            ),
            "intro": (
                "Распознанный текст размещается невидимо поверх изображения по точным "
                "координатам. Внешний вид страницы не меняется вообще."
            ),
        },
        "document-scanner": {
            "h1": "Сканирование документов камерой",
            "description": (
                "Превратите фотографии документов в ровные чистые страницы и объедините "
                "их в один PDF."
            ),
            "intro": (
                "Границы определяются автоматически, перспектива исправляется, тени "
                "убираются. Страницы можно менять местами, поворачивать и удалять."
            ),
        },
        "receipt-scanner": {
            "h1": "Распознавание чеков",
            "description": (
                "Извлеките продавца, дату, суммы, налог и позиции из чека в JSON, CSV или Excel."
            ),
            "intro": (
                "Отсутствующие в чеке поля возвращаются как null. Мы никогда не "
                "придумываем значение, чтобы заполнить пропуск."
            ),
        },
        "invoice-ocr": {
            "h1": "Распознавание счетов",
            "description": (
                "Считайте номер счёта, даты, суммы, налоги и позиции в структурированные данные."
            ),
            "intro": (
                "У каждого поля есть оценка уверенности и ссылка на область страницы, из "
                "которой оно взято, — проверка занимает один клик."
            ),
        },
        "batch": {
            "h1": "Пакетная обработка файлов",
            "description": (
                "Загрузите до 100 изображений или PDF с одними настройками и скачайте "
                "результаты одним ZIP-архивом с отчётом."
            ),
            "intro": (
                "Каждый файл обрабатывается независимо, поэтому один плохой скан не ломает "
                "весь пакет. Неудачные файлы попадают в отчёт и повторяются отдельно."
            ),
        },
    }
    table = ru if locale == "ru" else en
    return table.get(tool_slug, table["image-to-text"])


def _tool_faq(tool_slug: str, locale: str) -> list[dict[str, str]]:
    if locale == "ru":
        base = [
            {
                "q": "Сколько хранятся мои файлы?",
                "a": (
                    "Файлы гостей удаляются через 24 часа. У зарегистрированных "
                    "пользователей срок зависит от тарифа, и проект можно удалить в любой "
                    "момент вручную."
                ),
            },
            {
                "q": "Какие форматы поддерживаются?",
                "a": "JPG, PNG, WEBP, HEIC, TIFF, BMP, GIF и PDF. Тип файла проверяется по "
                "содержимому, а не по расширению.",
            },
            {
                "q": "Насколько это точно?",
                "a": (
                    "Точность зависит от качества исходника, языка и шрифта. Мы не обещаем "
                    "100%: вместо этого показываем оценку уверенности и подсвечиваем "
                    "сомнительные места."
                ),
            },
            {
                "q": "Используются ли мои файлы для обучения моделей?",
                "a": "Нет. Файлы не публикуются и не используются для обучения без "
                "отдельного явного согласия.",
            },
        ]
    else:
        base = [
            {
                "q": "How long are my files kept?",
                "a": (
                    "Guest uploads are deleted after 24 hours. For accounts the retention "
                    "period depends on your plan, and you can delete a project instantly "
                    "at any time."
                ),
            },
            {
                "q": "Which formats are supported?",
                "a": "JPG, PNG, WEBP, HEIC, TIFF, BMP, GIF and PDF. The type is detected "
                "from the file contents, not the extension.",
            },
            {
                "q": "How accurate is it?",
                "a": (
                    "Accuracy depends on the source quality, language and typeface. We do "
                    "not claim 100% — instead we show a confidence score and highlight the "
                    "parts worth checking."
                ),
            },
            {
                "q": "Are my files used to train models?",
                "a": "No. Files are never published and are not used for training without "
                "separate explicit consent.",
            },
        ]

    specific = {
        "image-to-excel": (
            {
                "q": "Что если в таблице нет линий?",
                "a": "Структура восстанавливается по "
                "расположению значений; если она неоднозначна, мы помечаем таблицу как "
                "требующую проверки.",
            }
            if locale == "ru"
            else {
                "q": "What if the table has no borders?",
                "a": "The structure is inferred "
                "from the position of each value. If it is ambiguous the table is flagged "
                "for review rather than exported as if it were certain.",
            }
        ),
        "handwriting-to-text": (
            {
                "q": "Распознаётся ли неразборчивый почерк?",
                "a": "Не гарантированно. Неуверенные фрагменты помечаются, чтобы вы их проверили.",
            }
            if locale == "ru"
            else {
                "q": "Does it read messy handwriting?",
                "a": "Not reliably. Uncertain "
                "words are marked so you can correct them instead of trusting them.",
            }
        ),
        "pdf-translator": (
            {
                "q": "Работает ли с защищёнными PDF?",
                "a": "Файлы с паролем не обрабатываются — снимите пароль и загрузите снова.",
            }
            if locale == "ru"
            else {
                "q": "Does it work with protected PDFs?",
                "a": "Password-protected files are rejected. Remove the password and upload again.",
            }
        ),
    }.get(tool_slug)
    return ([specific] + base) if specific else base


def seed_seo_pages(session: Session, *, force: bool = False) -> int:
    created = 0
    # Rows added during this run are not yet visible to a SELECT, so track them
    # here as well; otherwise two generators producing the same path would both
    # insert and trip the (path, locale) unique constraint at flush time.
    written: set[tuple[str, str]] = set()

    def upsert(path: str, locale: str, **fields: Any) -> None:
        nonlocal created
        if (path, locale) in written:
            log.warning("seed.duplicate_seo_path", path=path, locale=locale)
            return
        written.add((path, locale))

        row = session.execute(
            select(SeoPage).where(SeoPage.path == path, SeoPage.locale == locale)
        ).scalar_one_or_none()
        if row is not None and not force:
            return
        if row is None:
            row = SeoPage(path=path, locale=locale, title="", description="", h1="")
            session.add(row)
            created += 1
        for key, value in fields.items():
            setattr(row, key, value)

    for locale in ("en", "ru"):
        for tool in tool_catalog.TOOLS:
            copy = _tool_copy(tool.slug, locale)
            # Locales with their own slug publish only under it, so the English
            # path never becomes a competing duplicate in that language.
            upsert(
                f"/{tool_catalog.slug_for(tool, locale)}",
                locale,
                kind="tool",
                title=f"{copy['h1']} — {settings.brand_name}",
                description=copy["description"],
                h1=copy["h1"],
                intro=copy["intro"],
                tool_slug=tool.slug,
                body_sections=[
                    {"type": "formats", "items": list(tool.accepts)},
                    {"type": "exports", "items": [str(item) for item in tool.exports]},
                    {"type": "how_it_works", "key": f"how_it_works.{tool.i18n_key}"},
                    {"type": "privacy", "key": "privacy.short"},
                    {"type": "limits", "key": f"limits.{tool.i18n_key}"},
                ],
                faq=_tool_faq(tool.slug, locale),
                published=True,
            )

        # Language pair pages
        for source, target, page_locale in SEO_PAIRS:
            if page_locale != locale:
                continue
            source_language = language_table.get(source)
            target_language = language_table.get(target)
            if not (source_language and target_language):
                continue
            if locale == "ru":
                h1 = (
                    f"Перевод изображений с {source_language.name_native.lower()} "
                    f"на {target_language.name_native.lower()}"
                )
                description = (
                    f"Загрузите изображение с текстом на "
                    f"{source_language.name_native.lower()} и получите перевод на "
                    f"{target_language.name_native.lower()} прямо на картинке."
                )
                intro = (
                    f"Языковая пара выбрана заранее — просто загрузите файл. "
                    f"{_script_note(target_language, 'ru')}"
                )
            else:
                h1 = f"Translate images from {source_language.name_en} to {target_language.name_en}"
                description = (
                    f"Upload an image containing {source_language.name_en} text and get it "
                    f"back translated into {target_language.name_en}, in place."
                )
                intro = (
                    f"The language pair is preselected — just drop your file in. "
                    f"{_script_note(target_language, 'en')}"
                )
            upsert(
                f"/translate-image/{_slug(source_language.name_en)}-to-{_slug(target_language.name_en)}",
                locale,
                kind="language_pair",
                title=f"{h1} — {settings.brand_name}",
                description=description,
                h1=h1,
                intro=intro,
                tool_slug="image-translator",
                source_language=source_language.code,
                target_language=target_language.code,
                body_sections=[
                    {"type": "script_notes", "key": f"script.{target_language.script}"},
                    {"type": "how_it_works", "key": "how_it_works.image_translator"},
                    {"type": "privacy", "key": "privacy.short"},
                ],
                faq=_pair_faq(source_language, target_language, locale),
                published=True,
            )

        # Format pages
        for slug, tool_type, extension, output in tool_catalog.FORMAT_PAGES:
            tool = tool_catalog.BY_TYPE[tool_type]
            if locale == "ru":
                h1 = f"{extension.upper()} в {output.upper()}"
                description = (
                    f"Преобразуйте файлы {extension.upper()} в {output.upper()} онлайн: "
                    f"распознавание текста, сохранение структуры, бесплатный старт."
                )
                intro = (
                    f"Страница принимает именно {extension.upper()} и отдаёт "
                    f"{output.upper()}. Остальные поддерживаемые форматы тоже работают."
                )
            else:
                h1 = f"{extension.upper()} to {output.upper()}"
                description = (
                    f"Convert {extension.upper()} files to {output.upper()} online with "
                    f"text recognition and structure preserved."
                )
                intro = (
                    f"This page accepts {extension.upper()} and produces {output.upper()}. "
                    f"Other supported formats work here too."
                )
            upsert(
                f"/{slug}",
                locale,
                kind="format",
                title=f"{h1} — {settings.brand_name}",
                description=description,
                h1=h1,
                intro=intro,
                tool_slug=tool.slug,
                body_sections=[
                    {"type": "formats", "items": list(tool.accepts)},
                    {"type": "exports", "items": [str(item) for item in tool.exports]},
                    {"type": "how_it_works", "key": f"how_it_works.{tool.i18n_key}"},
                ],
                faq=_tool_faq(tool.slug, locale),
                published=True,
            )

    session.flush()
    return created


def _script_note(language: Any, locale: str) -> str:
    if locale == "ru":
        if language.is_rtl:
            return "Текст справа налево наносится с правильным порядком символов."
        if language.is_cjk:
            return "Поддерживается горизонтальное и вертикальное написание."
        return "Перевод обычно длиннее оригинала, поэтому размер шрифта подбирается автоматически."
    if language.is_rtl:
        return "Right-to-left text is drawn with correct character ordering and shaping."
    if language.is_cjk:
        return "Both horizontal and vertical writing are supported."
    return "Translations are often longer than the original, so font size is fitted automatically."


def _pair_faq(source: Any, target: Any, locale: str) -> list[dict[str, str]]:
    if locale == "ru":
        return [
            {
                "q": f"Сохранится ли вёрстка при переводе на {target.name_native.lower()}?",
                "a": (
                    "Да. Перевод наносится в те же области. Если текст стал длиннее, "
                    "размер шрифта уменьшается в разумных пределах, а при нехватке места "
                    "показывается предупреждение."
                ),
            },
            {
                "q": f"Нужно ли указывать, что исходный язык — {source.name_native.lower()}?",
                "a": "Нет, язык определяется автоматически, но на этой странице он уже выбран.",
            },
        ]
    return [
        {
            "q": f"Will the layout survive translation into {target.name_en}?",
            "a": (
                "Yes. The translation is drawn into the same areas. If it is longer, the "
                "font size is reduced within sensible limits, and you get a warning when "
                "text genuinely does not fit."
            ),
        },
        {
            "q": f"Do I have to tell it the source is {source.name_en}?",
            "a": "No — the language is detected automatically, and on this page it is "
            "already preselected.",
        },
    ]


def _slug(name: str) -> str:
    return (
        name.lower()
        .replace(" (simplified)", "-simplified")
        .replace(" (traditional)", "-traditional")
        .replace(" ", "-")
    )


def seed_blog(session: Session, *, force: bool = False) -> int:
    posts = [
        {
            "slug": "how-image-translation-works",
            "locale": "en",
            "title": "How translating text inside an image actually works",
            "excerpt": (
                "Recognition, layout reconstruction, background repair and typesetting — "
                "the four steps between your photo and a translated image."
            ),
            "tags": ["engineering", "ocr"],
            "reading_minutes": 6,
            "body_markdown": (
                "## Four steps, not one\n\n"
                'Translating an image is not "OCR plus a translation API". Four separate '
                "problems have to be solved, and each one can ruin the result.\n\n"
                "### 1. Recognition\n\n"
                "The engine returns lines, not paragraphs. Lines are grouped back into "
                "blocks by measuring vertical gaps against line height and horizontal "
                "overlap, and columns are found by looking for vertical gutters that no "
                "text crosses.\n\n"
                "### 2. Understanding the layout\n\n"
                "A heading is not just bigger text — it is bigger *relative to the body "
                "text*, and short. We compute the body size as a length-weighted median so "
                "that a two-word title cannot drag the average up and hide itself.\n\n"
                "### 3. Removing the original text\n\n"
                "Flat backgrounds are filled with the sampled paper colour, which is "
                "perfect. Textured backgrounds go through inpainting. Photographic "
                "backgrounds get a translucent plate, because a smeared repair looks worse "
                "than an honest one.\n\n"
                "### 4. Typesetting the translation\n\n"
                "German is often 35% longer than English; Chinese is roughly half the "
                "width. Font size is binary-searched against real glyph metrics — never an "
                "average character width — and we warn you when text genuinely does not fit."
            ),
        },
        {
            "slug": "kak-rabotaet-perevod-na-izobrazhenii",
            "locale": "ru",
            "title": "Как на самом деле работает перевод текста на картинке",
            "excerpt": (
                "Распознавание, разбор вёрстки, восстановление фона и типографика — "
                "четыре шага между фотографией и переведённым изображением."
            ),
            "tags": ["инженерия", "ocr"],
            "reading_minutes": 6,
            "body_markdown": (
                "## Четыре задачи, а не одна\n\n"
                "Перевод изображения — это не «OCR плюс переводчик». Нужно решить четыре "
                "разные задачи, и каждая может испортить результат.\n\n"
                "### 1. Распознавание\n\n"
                "Движок возвращает строки, а не абзацы. Строки собираются обратно в блоки "
                "по вертикальным промежуткам относительно высоты строки и по "
                "горизонтальному перекрытию.\n\n"
                "### 2. Разбор вёрстки\n\n"
                "Заголовок — это не просто крупный текст, а текст крупнее основного и "
                "короткий. Базовый размер считается как медиана, взвешенная по длине, "
                "чтобы заголовок из двух слов не поднял среднее и не спрятал сам себя.\n\n"
                "### 3. Удаление исходного текста\n\n"
                "Ровный фон заливается цветом бумаги — это идеально. Текстурный проходит "
                "через inpainting. На фотографическом фоне рисуется полупрозрачная "
                "подложка: честный компромисс лучше размазанного пятна.\n\n"
                "### 4. Нанесение перевода\n\n"
                "Немецкий обычно на 35% длиннее английского, китайский — примерно вдвое "
                "уже. Размер шрифта подбирается бинарным поиском по реальным метрикам "
                "глифов, а если текст всё равно не помещается, мы об этом предупреждаем."
            ),
        },
        {
            "slug": "why-we-show-confidence-not-accuracy",
            "locale": "en",
            "title": "Why we show confidence instead of claiming accuracy",
            "excerpt": (
                "An accuracy percentage without ground truth is a marketing number. "
                "Here is what our quality score actually measures."
            ),
            "tags": ["product", "quality"],
            "reading_minutes": 4,
            "body_markdown": (
                "## You cannot measure accuracy without the answer\n\n"
                'Claiming "99% accurate" requires knowing what the correct text was. On '
                "your document, we do not.\n\n"
                "What we *can* observe is the engine's own confidence, how many characters "
                "look typographically suspicious, whether language detection was stable, "
                "how much of the text was translated, whether the translation overflowed "
                "its box and how cleanly the background was repaired.\n\n"
                "Those signals are combined into one score, and every contributing factor "
                "is listed with a link to the blocks that caused it. A score of "
                '"review recommended" tells you where to look — which is more useful than '
                "a number that cannot be wrong because it was never measured."
            ),
        },
    ]

    created = 0
    for post in posts:
        row = session.execute(
            select(BlogPost).where(BlogPost.slug == post["slug"], BlogPost.locale == post["locale"])
        ).scalar_one_or_none()
        if row is not None and not force:
            continue
        if row is None:
            row = BlogPost(slug=post["slug"], locale=post["locale"])
            session.add(row)
            created += 1
        for key, value in post.items():
            if key not in {"slug", "locale"}:
                setattr(row, key, value)
        row.published_at = row.published_at or datetime.now(UTC)
    session.flush()
    return created


# --------------------------------------------------------------------------- #
# Demo accounts
# --------------------------------------------------------------------------- #
def seed_accounts(session: Session) -> dict[str, Any]:
    from picglot.services import auth as auth_service
    from picglot.services import credits as credit_service

    if settings.is_production:
        return {"skipped": "refused in production"}

    result: dict[str, Any] = {}
    for label, email, password, role, plan in (
        (
            "admin",
            settings.seed_admin_email,
            settings.seed_admin_password,
            AdminRole.SUPERADMIN,
            "business",
        ),
        ("demo", settings.seed_demo_email, settings.seed_demo_password, None, "pro"),
    ):
        user = auth_service.find_user(session, email)
        if user is None:
            user, _token = auth_service.register(
                session,
                email=email,
                password=password,
                name="Administrator" if role else "Demo user",
            )
        user.status = str(UserStatus.ACTIVE)
        user.email_verified_at = datetime.now(UTC)
        user.plan_code = plan
        if role:
            user.admin_role = str(role)
        wallet = credit_service.get_or_create_wallet(session, user_id=user.id)
        credit_service.grant_monthly(session, user=user, amount=500, period=f"seed-{plan}")
        result[label] = {"email": user.email, "balance": wallet.balance}

    session.flush()
    return result


def user_count(session: Session) -> int:
    from sqlalchemy import func

    return int(session.execute(select(func.count(User.id))).scalar_one())
