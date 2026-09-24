# FoodHub by Soni - simple food delivery website (Flask + SQLAlchemy)
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Live pe DATABASE_URL (Neon/Postgres) use hoga, local pe SQLite file
db_url = os.environ.get("DATABASE_URL", "sqlite:///foodhub.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
db = SQLAlchemy(app)

STATUSES = ["Placed", "Preparing", "Out for Delivery", "Delivered"]


# ---------- DATABASE TABLES ----------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)


class Restaurant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    cuisine = db.Column(db.String(60), nullable=False)
    emoji = db.Column(db.String(8), default="🍽️")
    rating = db.Column(db.Float, default=4.0)
    eta = db.Column(db.Integer, default=30)  # delivery minutes
    dishes = db.relationship("Dish", backref="restaurant")


class Dish(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurant.id"))
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    is_veg = db.Column(db.Boolean, default=True)
    emoji = db.Column(db.String(8), default="🍛")


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurant.id"))
    address = db.Column(db.String(255))
    phone = db.Column(db.String(20))
    total = db.Column(db.Integer)
    status = db.Column(db.String(30), default="Placed")
    created = db.Column(db.DateTime, server_default=db.func.now())
    items = db.relationship("OrderItem", backref="order")
    restaurant = db.relationship("Restaurant")
    user = db.relationship("User")


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    name = db.Column(db.String(100))
    price = db.Column(db.Integer)
    qty = db.Column(db.Integer)


# tables bana do (agar pehle se hain to kuch nahi hota)
with app.app_context():
    db.create_all()


# ---------- HELPERS ----------
def current_user():
    uid = session.get("user_id")
    if uid:
        return db.session.get(User, uid)
    return None


def get_cart():
    return session.get("cart", {"restaurant_id": None, "items": {}})


def cart_details():
    # cart ki lines + bill (subtotal, delivery fee, 5% tax, total)
    cart = get_cart()
    lines = []
    subtotal = 0
    for dish_id, qty in cart["items"].items():
        dish = db.session.get(Dish, int(dish_id))
        if dish:
            lines.append({"dish": dish, "qty": qty, "total": dish.price * qty})
            subtotal = subtotal + dish.price * qty
    fee = 0
    if 0 < subtotal < 500:
        fee = 30  # 500 se upar delivery free
    tax = round(subtotal * 0.05)
    return lines, subtotal, fee, tax, subtotal + fee + tax


@app.context_processor
def inject_globals():
    count = 0
    for qty in get_cart()["items"].values():
        count = count + qty
    return {"user": current_user(), "cart_count": count}


# ---------- CUSTOMER PAGES ----------
@app.route("/")
def home():
    q = request.args.get("q", "").strip()
    cuisine = request.args.get("cuisine", "").strip()
    query = Restaurant.query
    if q:
        query = query.filter(Restaurant.name.ilike("%" + q + "%"))
    if cuisine:
        query = query.filter(Restaurant.cuisine == cuisine)
    restaurants = query.order_by(Restaurant.rating.desc()).all()
    cuisines = []
    for r in Restaurant.query.all():
        if r.cuisine not in cuisines:
            cuisines.append(r.cuisine)
    return render_template("index.html", restaurants=restaurants,
                           cuisines=cuisines, q=q, cuisine=cuisine)


@app.route("/restaurant/<int:rid>")
def restaurant(rid):
    r = db.get_or_404(Restaurant, rid)
    only_veg = request.args.get("veg") == "1"
    dishes = []
    for d in r.dishes:
        if d.is_veg or not only_veg:
            dishes.append(d)
    return render_template("restaurant.html", r=r, dishes=dishes, only_veg=only_veg)


@app.route("/cart")
def cart():
    lines, subtotal, fee, tax, total = cart_details()
    return render_template("cart.html", lines=lines, subtotal=subtotal,
                           fee=fee, tax=tax, total=total)


@app.route("/cart/add/<int:dish_id>", methods=["POST"])
def cart_add(dish_id):
    dish = db.get_or_404(Dish, dish_id)
    cart_data = get_cart()
    if cart_data["items"] and cart_data["restaurant_id"] != dish.restaurant_id:
        cart_data = {"restaurant_id": None, "items": {}}
        flash("Cart clear ho gaya: ek time pe ek hi restaurant se order hota hai.")
    cart_data["restaurant_id"] = dish.restaurant_id
    key = str(dish_id)
    cart_data["items"][key] = cart_data["items"].get(key, 0) + 1
    session["cart"] = cart_data
    return redirect(request.referrer or url_for("home"))


@app.route("/cart/remove/<int:dish_id>", methods=["POST"])
def cart_remove(dish_id):
    cart_data = get_cart()
    key = str(dish_id)
    if key in cart_data["items"]:
        cart_data["items"][key] = cart_data["items"][key] - 1
        if cart_data["items"][key] <= 0:
            del cart_data["items"][key]
    session["cart"] = cart_data
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["POST"])
def checkout():
    user = current_user()
    if not user:
        flash("Order karne ke liye pehle login karo.")
        return redirect(url_for("login"))
    lines, subtotal, fee, tax, total = cart_details()
    if not lines:
        return redirect(url_for("cart"))
    order = Order(user_id=user.id, restaurant_id=get_cart()["restaurant_id"],
                  address=request.form["address"], phone=request.form["phone"],
                  total=total)
    db.session.add(order)
    db.session.flush()  # order.id mil jaye
    for line in lines:
        db.session.add(OrderItem(order_id=order.id, name=line["dish"].name,
                                 price=line["dish"].price, qty=line["qty"]))
    db.session.commit()
    session.pop("cart", None)
    return redirect(url_for("order_detail", oid=order.id))


@app.route("/orders")
def orders():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    my = Order.query.filter_by(user_id=user.id).order_by(Order.id.desc()).all()
    return render_template("orders.html", orders=my)


@app.route("/order/<int:oid>")
def order_detail(oid):
    user = current_user()
    order = db.get_or_404(Order, oid)
    if not user or (order.user_id != user.id and not user.is_admin):
        return redirect(url_for("login"))
    return render_template("order.html", order=order, statuses=STATUSES)


# ---------- LOGIN / REGISTER ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if User.query.filter_by(email=email).first():
            flash("Ye email pehle se registered hai.")
        else:
            admin_email = os.environ.get("ADMIN_EMAIL", "").lower()
            u = User(name=request.form["name"], email=email,
                     password_hash=generate_password_hash(request.form["password"]),
                     is_admin=(email == admin_email))
            db.session.add(u)
            db.session.commit()
            session["user_id"] = u.id
            return redirect(url_for("home"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(email=request.form["email"].strip().lower()).first()
        if u and check_password_hash(u.password_hash, request.form["password"]):
            session["user_id"] = u.id
            return redirect(url_for("home"))
        flash("Email ya password galat hai.")
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ---------- ADMIN PANEL ----------
def is_admin():
    u = current_user()
    return bool(u and u.is_admin)


@app.route("/admin")
def admin():
    if not is_admin():
        return redirect(url_for("login"))
    return render_template("admin.html", orders=Order.query.order_by(Order.id.desc()).all(),
                           restaurants=Restaurant.query.all(), statuses=STATUSES)


@app.route("/admin/order/<int:oid>", methods=["POST"])
def admin_order(oid):
    if is_admin():
        db.get_or_404(Order, oid).status = request.form["status"]
        db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/restaurant", methods=["POST"])
def admin_restaurant():
    if is_admin():
        db.session.add(Restaurant(name=request.form["name"], cuisine=request.form["cuisine"],
                                  emoji=request.form["emoji"] or "🍽️"))
        db.session.commit()
    return redirect(url_for("admin"))


@app.route("/admin/dish", methods=["POST"])
def admin_dish():
    if is_admin():
        db.session.add(Dish(restaurant_id=int(request.form["restaurant_id"]),
                            name=request.form["name"], price=int(request.form["price"]),
                            is_veg=(request.form["veg"] == "1"),
                            emoji=request.form["emoji"] or "🍛"))
        db.session.commit()
    return redirect(url_for("admin"))


# ---------- ONE-TIME SAMPLE DATA ----------
@app.route("/setup")
def setup():
    if request.args.get("key") != os.environ.get("SETUP_KEY", "soni123"):
        return "Wrong key", 403
    if Restaurant.query.count() == 0:
        a = Restaurant(name="Spice Garden", cuisine="North Indian", emoji="🍛", rating=4.5, eta=30)
        b = Restaurant(name="Pizza Point", cuisine="Italian", emoji="🍕", rating=4.2, eta=25)
        c = Restaurant(name="Dragon Bowl", cuisine="Chinese", emoji="🥡", rating=4.0, eta=35)
        db.session.add_all([a, b, c])
        db.session.flush()
        sample = [
            (a, "Paneer Butter Masala", 220, True, "🧀"), (a, "Butter Chicken", 280, False, "🍗"),
            (a, "Garlic Naan", 50, True, "🫓"), (b, "Margherita Pizza", 249, True, "🍕"),
            (b, "Chicken Pizza", 329, False, "🍕"), (c, "Veg Noodles", 150, True, "🍜"),
            (c, "Chicken Manchurian", 210, False, "🍢"),
        ]
        for r, n, p, v, e in sample:
            db.session.add(Dish(restaurant_id=r.id, name=n, price=p, is_veg=v, emoji=e))
        db.session.commit()
    return "Done! Sample data ready. <a href='/'>Home</a>"


if __name__ == "__main__":
    app.run(debug=True)
