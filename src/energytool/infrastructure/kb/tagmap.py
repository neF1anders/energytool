"""SectionTagger: раздел → (область, тип) по ручной карте базы Energytool.

Карта в коде (а не в файле данных), поэтому попадает в ZIP сабмита. Порядок разбора:
1. область: правило для (файл, заголовок в пути) → правило для файла → правило для
   каталога → Topic.GENERAL;
2. тип: первый совпавший заголовок, начиная с самого глубокого; затем тип по файлу;
   иначе OTHER.

Солнечные разделы помечены SOLAR_GRID: в базе один файл на оба типа станций,
гибрид получает их через scope_for() в domain/knowledge.py.
"""

from __future__ import annotations

from energytool.application.ports import RawSection
from energytool.domain.knowledge import Scope, SectionKind, Topic
from energytool.domain.products import Product

_FILE_SCOPES: dict[str, Scope] = {
    "00-product-map": Topic.GENERAL,
    "01-numbers-and-indicators": Topic.GENERAL,
    "02-product-scope-and-limits": Topic.GENERAL,
    "03-calculation-and-requests": Topic.GENERAL,
    "13-metering": Product.METERING,
    "15-microgeneration": Product.SOLAR_GRID,
    "20-portfolio": Topic.GENERAL,
    "21-orem": Product.OREM,
    "22-askue": Product.METERING,
    "23-solar": Product.SOLAR_GRID,
    "24-losses": Product.LOSSES,
    "25-meter-reading": Product.PHOTO,
    "26-general": Topic.GENERAL,
}

_DIRECTORY_SCOPES: dict[str, Scope] = {
    "10-energy": Topic.ENERGY,
    "20-products": Topic.GENERAL,
    "00-reference": Topic.GENERAL,
}

# (файл, заголовок в пути) → область: справочники, разбитые по продуктам
_HEADING_SCOPES: dict[tuple[str, str], Scope] = {
    ("01-numbers-and-indicators", "Выход на оптовый рынок"): Product.OREM,
    ("01-numbers-and-indicators", "Платформа учёта"): Product.METERING,
    ("01-numbers-and-indicators", "Солнечные станции"): Product.SOLAR_GRID,
    ("01-numbers-and-indicators", "Распознавание показаний"): Product.PHOTO,
    ("01-numbers-and-indicators", "Пороги, связанные с учётом"): Topic.ENERGY,
    ("02-product-scope-and-limits", "Выход на оптовый рынок"): Product.OREM,
    ("02-product-scope-and-limits", "Облачная платформа учёта"): Product.METERING,
    ("02-product-scope-and-limits", "Солнечные электростанции"): Product.SOLAR_GRID,
    ("02-product-scope-and-limits", "Поиск коммерческих потерь"): Product.LOSSES,
    ("02-product-scope-and-limits", "Распознавание показаний по фотографии"): Product.PHOTO,
    ("03-calculation-and-requests", "Для анализа затрат и выхода на оптовый рынок"): Product.OREM,
    ("03-calculation-and-requests", "Для солнечной станции"): Product.SOLAR_GRID,
    ("03-calculation-and-requests", "Для платформы учёта"): Product.METERING,
    ("03-calculation-and-requests", "Для поиска коммерческих потерь"): Product.LOSSES,
    ("03-calculation-and-requests", "Для распознавания показаний"): Product.PHOTO,
    ("12-power-thresholds", "Пороги оптового рынка"): Product.OREM,
}

# префикс заголовка → тип; проверяется от самого глубокого заголовка к корню
_HEADING_KINDS: tuple[tuple[str, SectionKind], ...] = (
    ("Не подходит", SectionKind.NOT_FITS),
    ("Подходит, если", SectionKind.FITS),
    ("Кому подходит", SectionKind.FITS),
    ("Лучший профиль клиента", SectionKind.FITS),
    ("Целевой диапазон мощности", SectionKind.FITS),
    ("Факты, которые можно утверждать", SectionKind.ALLOWED_FACTS),
    ("Чего утверждать нельзя", SectionKind.FORBIDDEN),
    ("Что мы не делаем", SectionKind.WHAT_WE_DO),
    ("Что делает МТС", SectionKind.WHAT_WE_DO),
    ("Когда требуется специалист", SectionKind.SPECIALIST),
    ("Частые вопросы", SectionKind.FAQ),
)

_FILE_KINDS: dict[str, SectionKind] = {
    "01-numbers-and-indicators": SectionKind.NUMBERS,
    "03-calculation-and-requests": SectionKind.REQUIREMENTS,
    "17-faq": SectionKind.FAQ,
}


class KbSectionTagger:
    def tag(self, section: RawSection) -> tuple[Scope, SectionKind]:
        stem = _stem(section.file)
        return self._scope(stem, section), self._kind(stem, section)

    @staticmethod
    def _scope(stem: str, section: RawSection) -> Scope:
        for heading in reversed(section.headings):
            scope = _HEADING_SCOPES.get((stem, heading))
            if scope is not None:
                return scope
        if stem in _FILE_SCOPES:
            return _FILE_SCOPES[stem]
        directory = section.file.split("/", 1)[0]
        return _DIRECTORY_SCOPES.get(directory, Topic.GENERAL)

    @staticmethod
    def _kind(stem: str, section: RawSection) -> SectionKind:
        for heading in reversed(section.headings):
            for prefix, kind in _HEADING_KINDS:
                if heading.startswith(prefix):
                    return kind
        return _FILE_KINDS.get(stem, SectionKind.OTHER)


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
