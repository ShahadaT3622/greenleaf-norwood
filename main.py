"""
GreenLeaf Organics – Norwood Store
Core Business Logic (Production Safe)
"""
try:
    from azure.cosmos import CosmosClient
    print("✅ azure-cosmos imported")
except Exception as e:
    print("❌ Failed to import azure-cosmos:", e)
    raise

# -------------------------
# Imports
# -------------------------
from azure.cosmos import CosmosClient
from dotenv import load_dotenv
import pyodbc
import os
from datetime import date
from pdf_utils import generate_invoice_pdf, generate_daily_summary_pdf

# -------------------------
# Load environment variables
# -------------------------
load_dotenv()

COSMOS_URI = os.getenv("COSMOS_URI")
COSMOS_KEY = os.getenv("COSMOS_KEY")
COSMOS_DB_NAME = os.getenv("COSMOS_DB_NAME")

SQL_SERVER = os.getenv("SQL_SERVER")
SQL_DATABASE = os.getenv("SQL_DATABASE")
SQL_USERNAME = os.getenv("SQL_USERNAME")
SQL_PASSWORD = os.getenv("SQL_PASSWORD")

# -------------------------
# Validate credentials
# -------------------------
if not COSMOS_URI or not COSMOS_KEY or not COSMOS_DB_NAME:
    raise RuntimeError("❌ Cosmos DB credentials not loaded")

if not SQL_SERVER or not SQL_DATABASE:
    raise RuntimeError("❌ SQL credentials not loaded")

# -------------------------
# Cosmos DB (lazy, Flask-safe)
# -------------------------
_cosmos_client = None
_database = None

def get_database():
    global _cosmos_client, _database
    if _database is None:
        _cosmos_client = CosmosClient(COSMOS_URI, COSMOS_KEY)
        _database = _cosmos_client.get_database_client(COSMOS_DB_NAME)
    return _database

def get_containers():
    db = get_database()
    return (
        db.get_container_client("products"),
        db.get_container_client("suppliers"),
        db.get_container_client("orders")
    )

# -------------------------
# Azure SQL (lazy, Flask-safe)
# -------------------------
_sql_conn = None
_sql_cursor = None

def get_sql_cursor():
    global _sql_conn, _sql_cursor
    if _sql_conn is None:
        _sql_conn = pyodbc.connect(
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            f"UID={SQL_USERNAME};"
            f"PWD={SQL_PASSWORD};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
            f"Connection Timeout=30;"
        )
        _sql_cursor = _sql_conn.cursor()
    return _sql_cursor

# -------------------------
# Supplier Management
# -------------------------
def add_supplier(supplier):
    _, suppliers, _ = get_containers()
    suppliers.upsert_item(supplier)

# -------------------------
# Product Management
# -------------------------
def add_product(product):
    products, _, _ = get_containers()
    products.upsert_item(product)

# -------------------------
# Staff Stock Adjustment
# -------------------------
def adjust_stock(product_id, adjustment):
    products, _, _ = get_containers()

    product = products.read_item(
        item=product_id,
        partition_key="norwood"
    )

    product["stock_quantity"] += adjustment
    if product["stock_quantity"] < 0:
        product["stock_quantity"] = 0

    products.replace_item(product["id"], product)
    return product["stock_quantity"]

# -------------------------
# Order Validation + Placement
# -------------------------
def validate_and_place_order(order, allow_preorder=False):
    products_container, _, orders_container = get_containers()
    warnings = []

    for item in order["items"]:
        product = products_container.read_item(
            item=item["product_id"],
            partition_key="norwood"
        )

        available = product["stock_quantity"]
        requested = item["quantity"]

        if requested > available:
            if not allow_preorder:
                return {
                    "status": "error",
                    "message": f"Only {available} unit(s) available.",
                    "available": available,
                    "preorder": True
                }
            warnings.append(
                f"Pre-order placed for {requested - available} unit(s)"
            )
            requested = available

        product["stock_quantity"] -= requested
        products_container.replace_item(product["id"], product)

        if product["stock_quantity"] < 10:
            warnings.append(
                f"Low stock alert: {product['name']} ({product['stock_quantity']} left)"
            )

    orders_container.upsert_item(order)
    invoice_filename = generate_invoice_pdf(order)

    return {
        "status": "success",
        "warnings": warnings,
        "order_id": order["id"],
        "invoice_filename": invoice_filename
    }

# -------------------------
# Daily Summary (Cosmos Aggregation)
# -------------------------
def generate_daily_summary(summary_date):
    _, _, orders_container = get_containers()

    orders = list(
        orders_container.query_items(
            query="SELECT * FROM c WHERE c.order_date = @date",
            parameters=[{"name": "@date", "value": summary_date}],
            enable_cross_partition_query=True
        )
    )

    total_orders = len(orders)
    total_revenue = sum(o.get("order_total", 0) for o in orders)

    product_totals = {}
    for order in orders:
        for item in order.get("items", []):
            product_totals[item["product_name"]] = (
                product_totals.get(item["product_name"], 0) + item["quantity"]
            )

    most_popular = (
        max(product_totals, key=product_totals.get)
        if product_totals else None
    )

    return {
        "date": summary_date,
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "most_popular_product": most_popular
    }

# -------------------------
# Store Summary in Azure SQL + PDF
# -------------------------
def store_summary(summary):
    try:
        cursor = get_sql_cursor()
        cursor.execute(
            """
            IF NOT EXISTS (
                SELECT 1 FROM daily_summary WHERE summary_date = ?
            )
            INSERT INTO daily_summary
                (summary_date, total_orders, total_revenue, most_popular_product)
            VALUES (?, ?, ?, ?)
            """,
            summary["date"],
            summary["date"],
            summary["total_orders"],
            summary["total_revenue"],
            summary["most_popular_product"]
        )
        _sql_conn.commit()
        generate_daily_summary_pdf(summary)
        return True
    except Exception as e:
        print("⚠ Azure SQL unavailable:", e)
        generate_daily_summary_pdf(summary)
        return False

# -------------------------
# CLI RESET + SEED (12 Products, 4 Suppliers)
# -------------------------
if __name__ == "__main__":

    products_container, suppliers_container, _ = get_containers()

    # 🔥 Clear existing Norwood data
    for item in products_container.query_items(
        query="SELECT * FROM c WHERE c.store_id='norwood'",
        enable_cross_partition_query=True
    ):
        products_container.delete_item(item["id"], partition_key="norwood")

    for item in suppliers_container.query_items(
        query="SELECT * FROM c WHERE c.store_id='norwood'",
        enable_cross_partition_query=True
    ):
        suppliers_container.delete_item(item["id"], partition_key="norwood")

    # ---- Suppliers (4) ----
    suppliers = [
        {"id": "sup_1", "name": "FarmFresh Organics", "contact_email": "farm@fresh.com", "categories_supplied": ["Fruit", "Vegetables"], "store_id": "norwood"},
        {"id": "sup_2", "name": "PureDairy Co", "contact_email": "sales@puredairy.com", "categories_supplied": ["Dairy"], "store_id": "norwood"},
        {"id": "sup_3", "name": "Bakery House", "contact_email": "orders@bakery.com", "categories_supplied": ["Bakery"], "store_id": "norwood"},
        {"id": "sup_4", "name": "Eco Pantry", "contact_email": "hello@ecopantry.com", "categories_supplied": ["Pantry"], "store_id": "norwood"}
    ]

    for s in suppliers:
        suppliers_container.upsert_item(s)

    # ---- Products (12) ----
    products = [
        ("apple", "Fruit", 3.5, "sup_1"),
        ("banana", "Fruit", 2.8, "sup_1"),
        ("carrot", "Vegetable", 1.9, "sup_1"),
        ("broccoli", "Vegetable", 2.5, "sup_1"),
        ("milk", "Dairy", 4.2, "sup_2"),
        ("cheese", "Dairy", 5.5, "sup_2"),
        ("bread", "Bakery", 3.0, "sup_3"),
        ("croissant", "Bakery", 2.7, "sup_3"),
        ("rice", "Pantry", 4.8, "sup_4"),
        ("pasta", "Pantry", 3.9, "sup_4"),
        ("olive_oil", "Pantry", 6.5, "sup_4"),
        ("honey", "Pantry", 5.2, "sup_4"),
    ]

    for i, (name, cat, price, sup) in enumerate(products, start=1):
        products_container.upsert_item({
            "id": f"prod_{i:03}",
            "name": name.replace("_", " ").title(),
            "category": cat,
            "price": price,
            "stock_quantity": 20,
            "supplier_id": sup,
            "store_id": "norwood"
        })

    print("✅ RESET COMPLETE: 12 products and 4 suppliers seeded")
