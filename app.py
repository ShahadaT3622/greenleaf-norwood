# =========================
# Startup diagnostics (Azure)
# =========================
import os
print("🚀 app.py loaded")
print("ENV CHECK:")
print("COSMOS_URI:", bool(os.getenv("COSMOS_URI")))
print("SQL_SERVER:", bool(os.getenv("SQL_SERVER")))

# =========================
# Imports
# =========================
from flask import (
    Flask, render_template, request,
    send_file, abort, redirect, url_for, session
)
from datetime import date
from functools import wraps

# Import core logic (after env check)
from main import (
    get_containers,
    validate_and_place_order,
    generate_daily_summary,
    store_summary,
    adjust_stock
)

# =========================
# Flask App
# =========================
app = Flask(__name__)
app.secret_key = "greenleaf-secret-key"

# -------------------------
# Absolute PDF directory
# -------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PDF_DIR = os.path.join(BASE_DIR, "pdfs")

# =========================
# Role Guard
# =========================
def role_required(role):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "role" not in session:
                return redirect(url_for("login"))
            if session["role"] != role:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator

# =========================
# Login / Logout
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        session["role"] = request.form["role"]
        return redirect(
            url_for("summary") if session["role"] == "admin"
            else url_for("index")
        )
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# =====================================================
# 🌐 CUSTOMER SELF-SERVICE (PUBLIC)
# =====================================================

@app.route("/customer")
def customer_order():
    products_container, _, _ = get_containers()
    products = list(
        products_container.query_items(
            query="SELECT * FROM c WHERE c.store_id='norwood'",
            enable_cross_partition_query=True
        )
    )
    return render_template("customer_order.html", products=products)

@app.route("/customer/submit", methods=["POST"])
def customer_submit():
    products_container, _, _ = get_containers()
    products = list(
        products_container.query_items(
            query="SELECT * FROM c WHERE c.store_id='norwood'",
            enable_cross_partition_query=True
        )
    )

    items, total = [], 0
    for p in products:
        qty = int(request.form.get(f"qty_{p['id']}", 0))
        if qty > 0:
            items.append({
                "product_id": p["id"],
                "product_name": p["name"],
                "quantity": qty,
                "unit_price": p["price"]
            })
            total += qty * p["price"]

    order_id = f"cust_order_{date.today()}_{request.form['email']}"

    order = {
        "id": order_id,
        "order_date": str(date.today()),
        "customer": {
            "name": request.form["name"],
            "email": request.form["email"]
        },
        "items": items,
        "order_total": total,
        "store_id": "norwood"
    }

    result = validate_and_place_order(
        order,
        allow_preorder=request.form.get("preorder") == "yes"
    )

    return render_template(
        "customer_result.html",
        result=result,
        invoice_filename=f"invoice_{order_id}.pdf"
    )

# -------------------------
# 🌐 CUSTOMER DAILY SUMMARY (READ-ONLY)
# -------------------------
@app.route("/customer/summary", methods=["GET", "POST"])
def customer_summary():
    selected_date = request.form.get("summary_date") or str(date.today())

    _, _, orders_container = get_containers()
    orders = list(
        orders_container.query_items(
            query="SELECT * FROM c WHERE c.order_date = @date",
            parameters=[{"name": "@date", "value": selected_date}],
            enable_cross_partition_query=True
        )
    )

    summary = generate_daily_summary(selected_date)

    return render_template(
        "customer_summary.html",
        summary=summary,
        orders=orders,
        selected_date=selected_date
    )

# =====================================================
# 👩‍💼 STAFF ORDER ENTRY
# =====================================================

@app.route("/")
def index():
    if "role" not in session:
        return redirect(url_for("login"))
    if session["role"] == "admin":
        return redirect(url_for("summary"))

    products_container, _, _ = get_containers()
    products = list(
        products_container.query_items(
            query="SELECT * FROM c WHERE c.store_id='norwood'",
            enable_cross_partition_query=True
        )
    )
    return render_template("index.html", products=products)

@app.route("/submit", methods=["POST"])
@role_required("staff")
def submit():
    products_container, _, _ = get_containers()
    products = list(
        products_container.query_items(
            query="SELECT * FROM c WHERE c.store_id='norwood'",
            enable_cross_partition_query=True
        )
    )

    items, total = [], 0
    for p in products:
        qty = int(request.form.get(f"qty_{p['id']}", 0))
        if qty > 0:
            items.append({
                "product_id": p["id"],
                "product_name": p["name"],
                "quantity": qty,
                "unit_price": p["price"]
            })
            total += qty * p["price"]

    order_id = f"order_{date.today()}_{request.form['email']}"

    order = {
        "id": order_id,
        "order_date": str(date.today()),
        "customer": {
            "name": request.form["name"],
            "email": request.form["email"]
        },
        "items": items,
        "order_total": total,
        "store_id": "norwood"
    }

    result = validate_and_place_order(
        order,
        allow_preorder=request.form.get("preorder") == "yes"
    )

    return render_template(
        "result.html",
        result=result,
        invoice_filename=f"invoice_{order_id}.pdf"
    )

# =====================================================
# 📦 STAFF INVENTORY
# =====================================================

@app.route("/inventory")
@role_required("staff")
def inventory():
    products_container, _, _ = get_containers()
    products = list(
        products_container.query_items(
            query="SELECT * FROM c WHERE c.store_id='norwood'",
            enable_cross_partition_query=True
        )
    )
    return render_template("inventory.html", products=products)

@app.route("/inventory/update", methods=["POST"])
@role_required("staff")
def inventory_update():
    adjust_stock(
        request.form["product_id"],
        int(request.form["adjustment"])
    )
    return redirect(url_for("inventory"))

# =====================================================
# 👑 ADMIN DAILY SUMMARY + HISTORY
# =====================================================

@app.route("/summary", methods=["GET", "POST"])
@role_required("admin")
def summary():
    selected_date = request.form.get("summary_date") or str(date.today())

    summary_data = generate_daily_summary(selected_date)
    sql_saved = store_summary(summary_data)

    _, _, orders_container = get_containers()
    orders = list(
        orders_container.query_items(
            query="SELECT * FROM c WHERE c.order_date = @date",
            parameters=[{"name": "@date", "value": selected_date}],
            enable_cross_partition_query=True
        )
    )

    return render_template(
        "summary.html",
        summary=summary_data,
        orders=orders,
        sql_saved=sql_saved,
        selected_date=selected_date
    )

# =====================================================
# 📈 ADMIN ANALYTICS (DATE FILTERED)
# =====================================================

@app.route("/charts", methods=["GET", "POST"])
@role_required("admin")
def charts():
    selected_date = request.form.get("analytics_date")

    products_container, _, orders_container = get_containers()

    products = list(
        products_container.query_items(
            query="SELECT * FROM c WHERE c.store_id='norwood'",
            enable_cross_partition_query=True
        )
    )

    stock_labels, stock_values, stock_colors = [], [], []
    low_stock_count = 0

    for p in products:
        stock_labels.append(p["name"])
        stock_values.append(p["stock_quantity"])
        if p["stock_quantity"] < 5:
            stock_colors.append("#c62828")
            low_stock_count += 1
        elif p["stock_quantity"] < 10:
            stock_colors.append("#ef6c00")
        else:
            stock_colors.append("#2e7d32")

    if selected_date:
        orders = list(
            orders_container.query_items(
                query="SELECT * FROM c WHERE c.order_date = @date",
                parameters=[{"name": "@date", "value": selected_date}],
                enable_cross_partition_query=True
            )
        )
    else:
        orders = list(
            orders_container.query_items(
                query="SELECT * FROM c",
                enable_cross_partition_query=True
            )
        )

    units_sold = 0
    sales_totals = {}
    trend_totals = {}

    for order in orders:
        units_sold += sum(i["quantity"] for i in order.get("items", []))
        trend_totals[order["order_date"]] = trend_totals.get(order["order_date"], 0) + 1
        for item in order.get("items", []):
            sales_totals[item["product_name"]] = (
                sales_totals.get(item["product_name"], 0) + item["quantity"]
            )

    return render_template(
        "charts.html",
        stock_labels=stock_labels,
        stock_values=stock_values,
        stock_colors=stock_colors,
        units_sold=units_sold,
        low_stock_count=low_stock_count,
        sales_labels=list(sales_totals.keys()),
        sales_values=list(sales_totals.values()),
        trend_labels=sorted(trend_totals.keys()),
        trend_values=[trend_totals[d] for d in sorted(trend_totals.keys())],
        selected_date=selected_date
    )

# =====================================================
# 📄 PDFs
# =====================================================

@app.route("/pdf/<filename>")
def serve_pdf(filename):
    file_path = os.path.join(PDF_DIR, filename)
    if not os.path.exists(file_path):
        abort(404)
    return send_file(file_path, mimetype="application/pdf")

# =====================================================
# Run App (local only)
# =====================================================
if __name__ == "__main__":
    app.run(debug=True)
