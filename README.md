# MedShare - Setup Guide

MedShare ek Flask website hai jaha near-expiry medicines discount pe becha
jaa sakta hai.

## Kaise Run Kare

1. Terminal isi `medshare` folder mein khol kar libraries install karo:
   ```
   pip install -r requirements.txt
   ```

2. App run karo:
   ```
   python app.py
   ```

3. Browser mein kholo:
   ```
   http://127.0.0.1:5000
   ```

4. Default Admin login:
   - Email: `admin@medshare.com`
   - Password: `admin123`

## Roles

- **Patient** – medicines buy kar sakta hai
- **Seller/Pharmacy** – medicines list karta hai (photo ke saath), apne orders manage karta hai
- **Admin** – naye sellers verify karta hai, naye listings ko approve/reject karta hai

## Naya Feature: Medicine Photo Upload

Seller jab medicine add kare, wo ek photo bhi upload kar sakta hai (PNG/JPG/WEBP, max 5MB).
Photo `static/uploads/` folder mein save hoti hai. Agar photo nahi daali toh
ek default 💊 icon dikhta hai card pe.

## Basic Flow

1. Seller register kare → Admin use verify kare
2. Seller medicine add kare (photo ke saath) → Admin approve kare
3. Medicine homepage pe live ho jaati hai
4. Patient order place kare → Seller order dashboard pe dekh ke status update kare
