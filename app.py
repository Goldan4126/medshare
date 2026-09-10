from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
import os
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'medshare-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///medshare.db'
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB max
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
db = SQLAlchemy(app)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ---------------- MODELS ----------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    shop_name = db.Column(db.String(150))              # pharmacy/shop name (sellers only)
    shop_photo = db.Column(db.String(255))              # pharmacy photo/logo (sellers only)
    phone = db.Column(db.String(20))                    # contact number (sellers only, for admin verification calls)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # patient, seller, admin
    verified = db.Column(db.Boolean, default=False)  # admin verifies sellers

    @property
    def display_name(self):
        if self.role == 'seller' and self.shop_name:
            return self.shop_name
        return self.name

    @property
    def avg_rating(self):
        ratings = [fb.rating for fb in Feedback.query.join(Order).join(Medicine)
                   .filter(Medicine.seller_id == self.id, Feedback.order_id.isnot(None)).all()]
        return round(sum(ratings) / len(ratings), 1) if ratings else None

    @property
    def review_count(self):
        return Feedback.query.join(Order).join(Medicine)\
                              .filter(Medicine.seller_id == self.id, Feedback.order_id.isnot(None)).count()

class Medicine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    generic_name = db.Column(db.String(150))          # helps in search (e.g. Paracetamol)
    category = db.Column(db.String(50))                # tablet / syrup / injection / etc.
    expiry_date = db.Column(db.Date, nullable=False)
    original_price = db.Column(db.Float, nullable=False)
    discount_percent = db.Column(db.Float, default=0)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    image_filename = db.Column(db.String(255))          # medicine photo uploaded by seller

    seller = db.relationship('User', backref='medicines')

    @property
    def discounted_price(self):
        return round(self.original_price * (1 - self.discount_percent / 100), 2)

    @property
    def days_to_expiry(self):
        return (self.expiry_date - date.today()).days

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    medicine_id = db.Column(db.Integer, db.ForeignKey('medicine.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    delivery_address = db.Column(db.String(300), nullable=False, default='')
    order_date = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='Pending')  # Pending, Confirmed, Out for Delivery, Delivered, Cancelled

    user = db.relationship('User', backref='orders')
    medicine = db.relationship('Medicine', backref='orders')

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=True, unique=True)  # null = general site feedback
    rating = db.Column(db.Integer, nullable=False)   # 1 to 5 stars
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='feedbacks')
    order = db.relationship('Order', backref=db.backref('feedback', uselist=False))

class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    medicine_id = db.Column(db.Integer, db.ForeignKey('medicine.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='cart_items')
    medicine = db.relationship('Medicine', backref='cart_items')

    @property
    def subtotal(self):
        return round(self.medicine.discounted_price * self.quantity, 2)

class WishlistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    medicine_id = db.Column(db.Integer, db.ForeignKey('medicine.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'medicine_id', name='uq_wishlist_user_medicine'),)

    user = db.relationship('User', backref='wishlist_items')
    medicine = db.relationship('Medicine', backref='wishlist_items')

# ---------------- HELPERS ----------------

def current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None

def get_expiring_medicines(seller_id, days=7):
    """Medicines belonging to a seller that are approved and expiring within `days` days."""
    cutoff = date.today() + timedelta(days=days)
    return Medicine.query.filter(
        Medicine.seller_id == seller_id,
        Medicine.status == 'approved',
        Medicine.expiry_date <= cutoff,
        Medicine.expiry_date >= date.today()
    ).order_by(Medicine.expiry_date.asc()).all()

@app.context_processor
def inject_user():
    user = current_user()
    expiring_count = 0
    cart_count = 0
    wishlist_ids = set()
    if user and user.role == 'seller':
        expiring_count = len(get_expiring_medicines(user.id))
    if user and user.role == 'patient':
        cart_count = CartItem.query.filter_by(user_id=user.id).count()
        wishlist_ids = {w.medicine_id for w in WishlistItem.query.filter_by(user_id=user.id).all()}
    return dict(current_user=user, expiring_count=expiring_count, cart_count=cart_count, wishlist_ids=wishlist_ids)

# ---------------- AUTH ROUTES ----------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        shop_name = request.form.get('shop_name', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form.get('confirm_password', '')
        role = request.form['role']

        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'danger')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered!', 'danger')
            return redirect(url_for('register'))

        if role == 'seller' and not shop_name:
            flash('Please enter your pharmacy/shop name', 'warning')
            return redirect(url_for('register'))

        if role == 'seller' and not phone:
            flash('Please enter a contact phone number so admin can verify your pharmacy', 'warning')
            return redirect(url_for('register'))

        # Handle optional shop photo upload
        shop_photo_filename = None
        if role == 'seller':
            photo = request.files.get('shop_photo')
            if photo and photo.filename:
                if allowed_file(photo.filename):
                    ext = photo.filename.rsplit('.', 1)[1].lower()
                    shop_photo_filename = f"{uuid.uuid4().hex}.{ext}"
                    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                    photo.save(os.path.join(app.config['UPLOAD_FOLDER'], shop_photo_filename))
                else:
                    flash('Shop photo must be PNG, JPG, JPEG or WEBP', 'warning')

        # Patients don't need verification; sellers need admin approval
        auto_verified = True if role == 'patient' else False

        user = User(name=name, shop_name=shop_name if role == 'seller' else None,
                    shop_photo=shop_photo_filename,
                    phone=phone if role == 'seller' else None,
                    email=email, password=generate_password_hash(password), role=role,
                    verified=auto_verified)
        db.session.add(user)
        db.session.commit()
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            flash(f'Welcome back, {user.name}!', 'success')
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'info')
    return redirect(url_for('index'))

# ---------------- MAIN ROUTES ----------------

@app.route('/')
def index():
    search = request.args.get('search', '')
    category = request.args.get('category', '')
    query = Medicine.query.filter_by(status='approved')
    if search:
        query = query.filter(db.or_(Medicine.name.ilike(f'%{search}%'),
                                     Medicine.generic_name.ilike(f'%{search}%')))
    if category:
        query = query.filter_by(category=category)
    medicines = query.order_by(Medicine.expiry_date.asc()).all()

    stats = {
        'users': User.query.count(),
        'medicines': Medicine.query.filter_by(status='approved').count(),
        'orders': Order.query.count()
    }
    featured = Medicine.query.filter_by(status='approved')\
                              .order_by(Medicine.discount_percent.desc()).limit(3).all()

    # Verified pharmacies with their live medicine count
    pharmacies = []
    for s in User.query.filter_by(role='seller', verified=True).all():
        count = Medicine.query.filter_by(seller_id=s.id, status='approved').count()
        if count > 0:
            pharmacies.append({'seller': s, 'count': count})
    pharmacies.sort(key=lambda p: p['count'], reverse=True)

    return render_template('index.html', medicines=medicines, stats=stats,
                            featured=featured, pharmacies=pharmacies)

@app.route('/pharmacy/<int:seller_id>')
def pharmacy_medicines(seller_id):
    seller = User.query.filter_by(id=seller_id, role='seller').first_or_404()
    medicines = Medicine.query.filter_by(seller_id=seller.id, status='approved')\
                               .order_by(Medicine.expiry_date.asc()).all()
    reviews = Feedback.query.join(Order).join(Medicine)\
                             .filter(Medicine.seller_id == seller.id, Feedback.order_id.isnot(None))\
                             .order_by(Feedback.created_at.desc()).all()
    return render_template('pharmacy_medicines.html', seller=seller, medicines=medicines, reviews=reviews)

@app.route('/medicine/<int:med_id>')
def medicine_detail(med_id):
    medicine = Medicine.query.get_or_404(med_id)
    return render_template('medicine_detail.html', medicine=medicine)

@app.route('/add-medicine', methods=['GET', 'POST'])
def add_medicine():
    user = current_user()
    if not user or user.role != 'seller':
        flash('Please login as a seller to list medicines', 'warning')
        return redirect(url_for('login'))
    if not user.verified:
        flash('Your seller account is pending admin verification. Please wait for approval.', 'warning')
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form['name']
        generic_name = request.form.get('generic_name', '')
        category = request.form.get('category', '')
        expiry_date = datetime.strptime(request.form['expiry_date'], '%Y-%m-%d').date()
        original_price = float(request.form['original_price'])
        discount_percent = float(request.form.get('discount_percent', 0))
        quantity = int(request.form['quantity'])

        # Handle optional photo upload
        image_filename = None
        photo = request.files.get('photo')
        if photo and photo.filename:
            if allowed_file(photo.filename):
                ext = photo.filename.rsplit('.', 1)[1].lower()
                image_filename = f"{uuid.uuid4().hex}.{ext}"
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
            else:
                flash('Photo must be PNG, JPG, JPEG or WEBP', 'warning')

        medicine = Medicine(seller_id=user.id, name=name, generic_name=generic_name,
                             category=category, expiry_date=expiry_date,
                             original_price=original_price,
                             discount_percent=discount_percent,
                             quantity=quantity, status='pending',
                             image_filename=image_filename)
        db.session.add(medicine)
        db.session.commit()

        flash('Medicine submitted for admin approval!', 'success')
        return redirect(url_for('index'))

    return render_template('add_medicine.html')

@app.route('/order/<int:med_id>', methods=['POST'])
def place_order(med_id):
    user = current_user()
    if not user:
        flash('Please login to place an order', 'warning')
        return redirect(url_for('login'))

    medicine = Medicine.query.get_or_404(med_id)
    qty = int(request.form.get('quantity', 1))
    address_line = request.form.get('address_line', '').strip()
    address_city = request.form.get('address_city', '').strip()
    address_district = request.form.get('address_district', '').strip()
    address_state = request.form.get('address_state', '').strip()
    address_pincode = request.form.get('address_pincode', '').strip()
    address_phone = request.form.get('address_phone', '').strip()

    if not (address_line and address_city and address_state and address_pincode and address_phone):
        flash('Please fill in your complete delivery address', 'warning')
        return redirect(url_for('medicine_detail', med_id=med_id))

    address_parts = [address_line, address_city]
    if address_district and address_district != address_city:
        address_parts.append(address_district)
    address_parts.append(f"{address_state} - {address_pincode}")
    address_parts.append(f"Phone: {address_phone}")
    address = ", ".join(address_parts)

    if qty > medicine.quantity:
        flash('Not enough stock available', 'danger')
        return redirect(url_for('medicine_detail', med_id=med_id))

    order = Order(user_id=user.id, medicine_id=med_id, quantity=qty,
                  delivery_address=address, status='Pending')
    medicine.quantity -= qty
    db.session.add(order)
    db.session.commit()
    flash('Order placed! The seller has been notified.', 'success')
    return redirect(url_for('my_orders'))

# ---------------- CART ROUTES ----------------

@app.route('/cart/add/<int:med_id>', methods=['POST'])
def add_to_cart(med_id):
    user = current_user()
    if not user:
        flash('Please login to add items to cart', 'warning')
        return redirect(url_for('login'))
    medicine = Medicine.query.get_or_404(med_id)
    qty = int(request.form.get('quantity', 1))
    if qty > medicine.quantity:
        flash('Not enough stock available', 'danger')
        return redirect(url_for('medicine_detail', med_id=med_id))

    existing = CartItem.query.filter_by(user_id=user.id, medicine_id=med_id).first()
    if existing:
        existing.quantity = min(existing.quantity + qty, medicine.quantity)
    else:
        db.session.add(CartItem(user_id=user.id, medicine_id=med_id, quantity=qty))
    db.session.commit()
    flash(f'{medicine.name} added to cart', 'success')
    return redirect(url_for('medicine_detail', med_id=med_id))

@app.route('/cart')
def view_cart():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    items = CartItem.query.filter_by(user_id=user.id).all()
    total = round(sum(i.subtotal for i in items), 2)
    return render_template('cart.html', items=items, total=total)

@app.route('/cart/update/<int:item_id>', methods=['POST'])
def update_cart_item(item_id):
    user = current_user()
    item = CartItem.query.get_or_404(item_id)
    if not user or item.user_id != user.id:
        return redirect(url_for('login'))
    qty = int(request.form.get('quantity', 1))
    item.quantity = max(1, min(qty, item.medicine.quantity))
    db.session.commit()
    return redirect(url_for('view_cart'))

@app.route('/cart/remove/<int:item_id>', methods=['POST'])
def remove_cart_item(item_id):
    user = current_user()
    item = CartItem.query.get_or_404(item_id)
    if not user or item.user_id != user.id:
        return redirect(url_for('login'))
    db.session.delete(item)
    db.session.commit()
    flash('Removed from cart', 'info')
    return redirect(url_for('view_cart'))

@app.route('/cart/checkout', methods=['POST'])
def cart_checkout():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    items = CartItem.query.filter_by(user_id=user.id).all()
    if not items:
        flash('Your cart is empty', 'warning')
        return redirect(url_for('view_cart'))

    address_line = request.form.get('address_line', '').strip()
    address_city = request.form.get('address_city', '').strip()
    address_district = request.form.get('address_district', '').strip()
    address_state = request.form.get('address_state', '').strip()
    address_pincode = request.form.get('address_pincode', '').strip()
    address_phone = request.form.get('address_phone', '').strip()

    if not (address_line and address_city and address_state and address_pincode and address_phone):
        flash('Please fill in your complete delivery address', 'warning')
        return redirect(url_for('view_cart'))

    address_parts = [address_line, address_city]
    if address_district and address_district != address_city:
        address_parts.append(address_district)
    address_parts.append(f"{address_state} - {address_pincode}")
    address_parts.append(f"Phone: {address_phone}")
    address = ", ".join(address_parts)

    for item in items:
        if item.quantity > item.medicine.quantity:
            flash(f'Not enough stock for {item.medicine.name}', 'danger')
            return redirect(url_for('view_cart'))

    for item in items:
        order = Order(user_id=user.id, medicine_id=item.medicine_id, quantity=item.quantity,
                      delivery_address=address, status='Pending')
        item.medicine.quantity -= item.quantity
        db.session.add(order)
        db.session.delete(item)
    db.session.commit()
    flash('Order placed for all items in your cart!', 'success')
    return redirect(url_for('my_orders'))

# ---------------- WISHLIST ROUTES ----------------

@app.route('/wishlist/toggle/<int:med_id>', methods=['POST'])
def toggle_wishlist(med_id):
    user = current_user()
    if not user:
        flash('Please login to save items', 'warning')
        return redirect(url_for('login'))
    medicine = Medicine.query.get_or_404(med_id)
    existing = WishlistItem.query.filter_by(user_id=user.id, medicine_id=med_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        flash(f'Removed {medicine.name} from wishlist', 'info')
    else:
        db.session.add(WishlistItem(user_id=user.id, medicine_id=med_id))
        db.session.commit()
        flash(f'Saved {medicine.name} to wishlist', 'success')
    return redirect(request.referrer or url_for('medicine_detail', med_id=med_id))

@app.route('/wishlist')
def view_wishlist():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    items = WishlistItem.query.filter_by(user_id=user.id).all()
    return render_template('wishlist.html', items=items)

@app.route('/my-orders')
def my_orders():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    orders = Order.query.filter_by(user_id=user.id).order_by(Order.order_date.desc()).all()
    return render_template('orders.html', orders=orders)

@app.route('/order/<int:order_id>/cancel', methods=['POST'])
def cancel_order(order_id):
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    order = Order.query.get_or_404(order_id)

    if order.user_id != user.id:
        flash('You can only cancel your own orders', 'danger')
        return redirect(url_for('my_orders'))

    if order.status != 'Pending':
        flash('This order has already been confirmed by the seller and can no longer be cancelled', 'warning')
        return redirect(url_for('my_orders'))

    # Restore stock back to the medicine
    order.medicine.quantity += order.quantity
    order.status = 'Cancelled'
    db.session.commit()
    flash('Order cancelled successfully', 'info')
    return redirect(url_for('my_orders'))

# ---------------- SELLER ROUTES ----------------

@app.route('/seller/orders')
def seller_orders():
    user = current_user()
    if not user or user.role != 'seller':
        return redirect(url_for('login'))
    orders = Order.query.join(Medicine).filter(Medicine.seller_id == user.id)\
                         .order_by(Order.order_date.desc()).all()
    expiring_medicines = get_expiring_medicines(user.id)
    return render_template('seller_orders.html', orders=orders, expiring_medicines=expiring_medicines)

@app.route('/seller/order/<int:order_id>/update', methods=['POST'])
def update_order_status(order_id):
    user = current_user()
    order = Order.query.get_or_404(order_id)
    if not user or order.medicine.seller_id != user.id:
        return redirect(url_for('login'))
    if order.status == 'Cancelled':
        flash('This order was cancelled by the patient and cannot be updated', 'warning')
        return redirect(url_for('seller_orders'))
    order.status = request.form['status']
    db.session.commit()
    flash('Order status updated', 'success')
    return redirect(url_for('seller_orders'))

@app.route('/seller/analytics')
def seller_analytics():
    user = current_user()
    if not user or user.role != 'seller':
        return redirect(url_for('login'))

    orders = Order.query.join(Medicine).filter(Medicine.seller_id == user.id).all()

    total_orders = len(orders)
    total_units = sum(o.quantity for o in orders)
    total_revenue = round(sum(o.quantity * o.medicine.discounted_price for o in orders
                               if o.status != 'Cancelled'), 2)

    status_counts = {'Pending': 0, 'Confirmed': 0, 'Out for Delivery': 0, 'Delivered': 0, 'Cancelled': 0}
    for o in orders:
        status_counts[o.status] = status_counts.get(o.status, 0) + 1

    med_breakdown = {}
    for o in orders:
        if o.status == 'Cancelled':
            continue
        name = o.medicine.name
        if name not in med_breakdown:
            med_breakdown[name] = {'units': 0, 'revenue': 0}
        med_breakdown[name]['units'] += o.quantity
        med_breakdown[name]['revenue'] += o.quantity * o.medicine.discounted_price
    top_medicines = sorted(med_breakdown.items(), key=lambda x: x[1]['units'], reverse=True)[:5]
    max_units = max((m[1]['units'] for m in top_medicines), default=1)

    return render_template('seller_analytics.html', total_orders=total_orders,
                            total_units=total_units, total_revenue=total_revenue,
                            status_counts=status_counts, top_medicines=top_medicines,
                            max_units=max_units, avg_rating=user.avg_rating,
                            review_count=user.review_count)

# ---------------- FEEDBACK ROUTES ----------------

@app.route('/feedback', methods=['GET', 'POST'])
def site_feedback():
    """General feedback about MedShare as a platform (not tied to any specific order)."""
    user = current_user()
    if not user:
        flash('Please login to share your feedback', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        rating = int(request.form.get('rating', 5))
        message = request.form.get('message', '').strip()
        if not message:
            flash('Please write a message before submitting', 'warning')
            return redirect(url_for('site_feedback'))

        fb = Feedback(user_id=user.id, order_id=None, rating=rating, message=message)
        db.session.add(fb)
        db.session.commit()
        flash('Thank you! Your feedback has been submitted.', 'success')
        return redirect(url_for('site_feedback'))

    my_feedback = Feedback.query.filter_by(user_id=user.id, order_id=None)\
                                 .order_by(Feedback.created_at.desc()).all()
    return render_template('feedback.html', my_feedback=my_feedback)

@app.route('/order/<int:order_id>/feedback', methods=['GET', 'POST'])
def order_feedback(order_id):
    """Feedback for a specific delivered order."""
    user = current_user()
    if not user:
        flash('Please login to share your feedback', 'warning')
        return redirect(url_for('login'))

    order = Order.query.get_or_404(order_id)
    if order.user_id != user.id:
        flash('You can only give feedback on your own orders', 'danger')
        return redirect(url_for('my_orders'))
    if order.status != 'Delivered':
        flash('You can give feedback once your order has been delivered', 'warning')
        return redirect(url_for('my_orders'))
    if order.feedback:
        flash('You have already given feedback for this order', 'info')
        return redirect(url_for('my_orders'))

    if request.method == 'POST':
        rating = int(request.form.get('rating', 5))
        message = request.form.get('message', '').strip()
        if not message:
            flash('Please write a message before submitting', 'warning')
            return redirect(url_for('order_feedback', order_id=order.id))

        fb = Feedback(user_id=user.id, order_id=order.id, rating=rating, message=message)
        db.session.add(fb)
        db.session.commit()
        flash('Thank you for your feedback!', 'success')
        return redirect(url_for('my_orders'))

    return render_template('order_feedback.html', order=order)

# ---------------- ADMIN ROUTES ----------------

@app.route('/admin')
def admin_dashboard():
    user = current_user()
    if not user or user.role != 'admin':
        return redirect(url_for('login'))
    pending = Medicine.query.filter_by(status='pending').all()
    unverified_users = User.query.filter(User.role == 'seller',
                                          User.verified == False).all()
    all_feedback = Feedback.query.order_by(Feedback.created_at.desc()).limit(10).all()

    # Seller-wise sales report: total orders, total units sold, total revenue
    sellers = User.query.filter_by(role='seller').all()
    seller_sales = []
    for s in sellers:
        seller_orders = Order.query.join(Medicine).filter(Medicine.seller_id == s.id).all()
        total_orders = len(seller_orders)
        total_units = sum(o.quantity for o in seller_orders)
        total_revenue = sum(o.quantity * o.medicine.discounted_price for o in seller_orders)
        delivered_orders = sum(1 for o in seller_orders if o.status == 'Delivered')
        seller_sales.append({
            'seller': s,
            'total_orders': total_orders,
            'total_units': total_units,
            'total_revenue': round(total_revenue, 2),
            'delivered_orders': delivered_orders
        })
    # Sort by highest revenue first
    seller_sales.sort(key=lambda x: x['total_revenue'], reverse=True)

    return render_template('admin_dashboard.html', pending=pending,
                            unverified_users=unverified_users, all_feedback=all_feedback,
                            seller_sales=seller_sales)

@app.route('/admin/user/<int:user_id>/verify')
def verify_user(user_id):
    admin = current_user()
    if not admin or admin.role != 'admin':
        return redirect(url_for('login'))
    user = User.query.get_or_404(user_id)
    user.verified = True
    db.session.commit()
    flash(f'{user.name} has been verified', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/medicine/<int:med_id>/<action>')
def review_medicine(med_id, action):
    user = current_user()
    if not user or user.role != 'admin':
        return redirect(url_for('login'))
    medicine = Medicine.query.get_or_404(med_id)
    medicine.status = 'approved' if action == 'approve' else 'rejected'
    db.session.commit()
    flash(f'Medicine {action}d', 'info')
    return redirect(url_for('admin_dashboard'))

# ---------------- MAIN ----------------

with app.app_context():
    db.create_all()
    # create a default admin if none exists
    if not User.query.filter_by(role='admin').first():
        admin = User(name='Admin', email='admin@medshare.com',
                     password=generate_password_hash('admin123'), role='admin')
        db.session.add(admin)
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)
