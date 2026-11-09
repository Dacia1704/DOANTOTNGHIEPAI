"""Exact matches first; explicitly labelled nearby/price alternatives use real DB data."""

import math

from sqlalchemy import select

from app.chat.search import (
    FilterResolutionError, fetch_listing_cards, resolve_amenity_ids, resolve_locations,
)
from app.chat.service import cards_to_payloads
from app.db.models_core import Property, Ward
from app.recommend.scoring import haversine_km


def _nearby_properties(session, ward_ids, province_ids, radius_km):
    centers = session.execute(select(Ward.name, Ward.centroid_lat, Ward.centroid_lng)
                              .where(Ward.id.in_(ward_ids))).all()
    # Never invent a center or silently choose among ambiguous administrative units.
    if len(centers) != 1 or centers[0].centroid_lat is None or centers[0].centroid_lng is None:
        return {}, None
    center = centers[0]
    lat, lng = float(center.centroid_lat), float(center.centroid_lng)
    lat_delta = radius_km / 110.574
    lng_delta = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    stmt = select(Property.id, Property.latitude, Property.longitude).where(
        Property.ward_id.not_in(ward_ids),
        Property.latitude.between(lat - lat_delta, lat + lat_delta),
        Property.longitude.between(lng - lng_delta, lng + lng_delta),
    )
    if province_ids:
        stmt = stmt.where(Property.province_id.in_(province_ids))
    distances = {}
    for row in session.execute(stmt):
        distance = haversine_km(lat, lng, row.latitude, row.longitude)
        if distance <= radius_km:
            distances[row.id] = distance
    return distances, center.name


def search_with_suggestions(session, filters, *, radius_km=3, price_tolerance_pct=20, limit=8):
    wards, provinces = resolve_locations(session, filters.ward_or_area, filters.province)
    amenities, unknown = resolve_amenity_ids(session, filters.amenities)
    if unknown:
        raise FilterResolutionError("Bạn vui lòng nói rõ tiện ích cần tìm: " + ", ".join(unknown))

    def fetch(*, nearby=None, tolerance=0):
        return fetch_listing_cards(
            session, filters, ward_ids=wards if nearby is None else None,
            province_ids=provinces, amenity_ids=amenities, property_distances=nearby,
            price_tolerance_pct=tolerance, limit=limit,
        )

    exact = fetch()
    if exact:
        return cards_to_payloads(exact, session)

    distances, reference = _nearby_properties(session, wards, provinces, radius_km) if wards else ({}, None)
    stages = []
    if distances:
        stages.append(("NEARBY", distances, 0))
    if price_tolerance_pct and (filters.price_min is not None or filters.price_max is not None):
        stages.append(("PRICE", None, price_tolerance_pct))
        if distances:
            stages.append(("NEARBY_PRICE", distances, price_tolerance_pct))
    for kind, nearby, tolerance in stages:
        cards = fetch(nearby=nearby, tolerance=tolerance)
        if not cards:
            continue
        payloads = cards_to_payloads(cards, session)
        for card, item in zip(cards, payloads):
            differences = []
            if nearby is not None:
                distance = nearby[card.property_id]
                item.update(distance_km=round(distance, 2), reference_area=reference,
                            nearby_radius_km=radius_km)
                differences.append(f"khác phường yêu cầu, cách tâm {reference} khoảng {distance:.1f} km đường thẳng")
            if filters.price_max is not None and card.price > filters.price_max:
                differences.append(f"vượt ngân sách {float(card.price) - filters.price_max:,.0f} đồng/tháng")
            if filters.price_min is not None and card.price < filters.price_min:
                differences.append("giá thấp hơn khoảng giá yêu cầu")
            item.update(match_type=kind, differences=differences)
        return payloads
    return []


def compose_suggestions(listings, filters, *, price_tolerance_pct=20):
    """Deterministic explanation prevents an LLM calling relaxed matches exact matches."""
    if not listings:
        question = "Bạn muốn đổi phường hoặc cho biết một địa điểm cụ thể để tìm gần đó không?"
        if filters.price_max is not None and price_tolerance_pct:
            ceiling = filters.price_max * (1 + price_tolerance_pct / 100)
            question = (f"Mình đã kiểm tra cả mức giá tới {ceiling / 1_000_000:g} triệu/tháng. "
                        "Bạn cho biết mức giá tối đa mới hoặc tên phường khác muốn tìm nhé?")
        required = f" Mình vẫn giữ yêu cầu {', '.join(filters.amenities)}." if filters.amenities else ""
        return "Mình chưa tìm thấy phòng khớp yêu cầu hoặc phương án gần phù hợp." + required + " " + question
    lines = ["Chưa có phòng khớp toàn bộ yêu cầu. Mình tìm được một số phương án để bạn cân nhắc:"]
    for index, item in enumerate(listings[:5], 1):
        amenities = ", ".join(item["amenities"])
        lines.append(f"{index}. {item['title']} — {item['price_text']}, {item['ward_text']}"
                     + (f", {amenities}" if amenities else "")
                     + ". Lưu ý: " + "; ".join(item["differences"]) + ".")
    required = f"vẫn giữ {', '.join(filters.amenities)} và " if filters.amenities else ""
    lines.append(f"Bạn muốn {required}ưu tiên đúng khu vực hay đúng ngân sách?")
    return "\n".join(lines)
