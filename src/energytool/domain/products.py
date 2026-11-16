"""Справочники предметной области: продукты, сегменты клиентов, цели."""

from __future__ import annotations

from enum import StrEnum


class Product(StrEnum):
    """Шесть направлений из базы знаний (20-products/21..25)."""

    OREM = "orem"  # Выход на оптовый рынок под ключ (21-orem)
    METERING = "metering"  # Облачная платформа учёта, АСКУЭ (22-askue)
    SOLAR_GRID = "solar_grid"  # Сетевая солнечная станция (23-solar)
    SOLAR_HYBRID = "solar_hybrid"  # Гибридная станция с накопителями (23-solar)
    LOSSES = "losses"  # Поиск коммерческих потерь (24-losses)
    PHOTO = "photo"  # Распознавание показаний по фото (25-meter-reading)


ALL_PRODUCTS: tuple[Product, ...] = tuple(Product)


class Segment(StrEnum):
    """Тип организации клиента. Навигация по типу компании — 20-portfolio."""

    INDUSTRY = "industry"  # промышленное предприятие, производство
    WAREHOUSE = "warehouse"  # склад, складской комплекс
    RETAIL_CHAIN = "retail_chain"  # ретейл, общепит, сетевой бизнес
    BUSINESS_CENTER = "business_center"  # торговый или бизнес-центр, арендодатель
    OFFICE_SERVICES = "office_services"  # офис, услуги, ИТ без производственной нагрузки
    HOUSING = "housing"  # УК, ТСЖ, СНТ, товарищество
    GRID_COMPANY = "grid_company"  # электросетевая компания
    SALES_COMPANY = "sales_company"  # энергосбытовая компания
    RESOURCE_SUPPLY = "resource_supply"  # ресурсоснабжающая организация
    DEVELOPER = "developer"  # застройщик
    AGRO = "agro"  # агропромышленный объект
    OTHER = "other"


class Goal(StrEnum):
    """Задача клиента — 03-calculation-and-requests, «Для первичного понимания задачи»."""

    REDUCE_BILL = "reduce_bill"  # снижение платежа
    METERING = "metering"  # автоматизация учёта
    LOSS_DETECTION = "loss_detection"  # поиск потерь
    RELIABILITY = "reliability"  # надёжность электроснабжения
    OTHER = "other"
