import sqlite3
import hashlib
import math
import logging
import json
import asyncio
import streamlit as st
import pandas as pd
import os
from datetime import datetime, timedelta
from typing import Dict, List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

def init_database():
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()

    # Ensure users table exists
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        points INTEGER DEFAULT 0,
        referral_code TEXT UNIQUE,
        referred_by INTEGER,
        wallet_address TEXT
    )
    ''')

    # Ensure messages table exists
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'pending',
        admin_reply TEXT,
        replied_by INTEGER
    )
    ''')

    # Ensure banned words table exists
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS banned_words (
        word TEXT PRIMARY KEY,
        added_by INTEGER,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Ensure administrators table exists
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS administrators (
        admin_id INTEGER PRIMARY KEY,
        is_main_admin INTEGER DEFAULT 0,
        added_by INTEGER,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Ensure muted_users table exists
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS muted_users (
        user_id INTEGER PRIMARY KEY,
        muted_until TIMESTAMP,
        muted_by INTEGER
    )
    ''')

    # Ensure admin_settings table exists with display_mode column
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS admin_settings (
        admin_id INTEGER PRIMARY KEY,
        display_mode TEXT DEFAULT 'user_id'
    )
    ''')

    conn.commit()
    conn.close()


# Start command
async def referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT referral_code FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        referral_code = result[0]
        referral_link = f"https://t.me/test123zekpotbot?start={referral_code}"
        
        await update.message.reply_text(
            f"Your unique referral link is:\n{referral_link}\n\n"
            "Share this link to earn 1500 points for each new user!"
        )
    else:
        await update.message.reply_text("User not found. Please /start first to register the user.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    # Check if user exists
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    existing_user = cursor.fetchone()
    
    if not existing_user:
        # Generate new referral code
        referral_code = generate_referral_code(user_id)
        
        # Check if there's a referral code in the start command
        if context.args and len(context.args) > 0:
            referral_code_input = context.args[0]
            
            # Find the referrer
            cursor.execute('SELECT user_id FROM users WHERE referral_code = ?', (referral_code_input,))
            referrer = cursor.fetchone()
            
            if referrer and referrer[0] != user_id:  # Prevent self-referral
                # Add points to referrer and notify
                referrer_id = referrer[0]
                cursor.execute('''
                    UPDATE users 
                    SET points = points + 1500 
                    WHERE user_id = ?
                ''', (referrer_id,))
                
                # Notify referrer about point addition
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id, 
                        text=f"🎉 Congratulations! A new user joined using your referral link! You earned 1500 points!"
                    )
                except Exception as e:
                    logger.error(f"Could not send notification to referrer: {e}")
                
                # Create new user with referrer tracking
                cursor.execute('''
                    INSERT INTO users (user_id, points, referral_code, referred_by) 
                    VALUES (?, 5000, ?, ?)
                ''', (user_id, referral_code, referrer_id))
                
                await update.message.reply_text(
                    f"Welcome! You've been given 5000 points for starting and joined through a referral!\n"
                    f"Your unique referral link is: https://t.me/test123zekpotbot?start={referral_code}"
                )
            else:
                # Create user without referrer
                cursor.execute('''
                    INSERT INTO users (user_id, points, referral_code) 
                    VALUES (?, 5000, ?)
                ''', (user_id, referral_code))
                
                await update.message.reply_text(
                    f"Welcome! You've been given 5000 starting points!\n"
                    f"Your unique referral link is: https://t.me/test123zekpotbot?start={referral_code}"
                )
        else:
            # Create user without referrer
            cursor.execute('''
                INSERT INTO users (user_id, points, referral_code) 
                VALUES (?, 5000, ?)
            ''', (user_id, referral_code))
            
            await update.message.reply_text(
                f"Welcome! You've been given 5000 starting points!\n"
                f"Your unique referral link is: https://t.me/test123zekpotbot?start={referral_code}"
            )
    else:
        await update.message.reply_text(
            "Welcome back! Use /balance to check your points or /referral to get your referral link."
        )
    
    conn.commit()
    conn.close()

WAITING_FOR_WALLET = 1

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the wallet settings conversation."""
    await update.message.reply_text(
        "Please send me your wallet address.\n"
        "Or send /cancel to cancel the operation."
    )
    return WAITING_FOR_WALLET

async def handle_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the wallet address input."""
    user_id = update.effective_user.id
    wallet_address = update.message.text.strip()

    if not wallet_address:
        await update.message.reply_text("Invalid wallet address. Please try again or use /cancel to cancel.")
        return WAITING_FOR_WALLET

    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()

    try:
        # Ensure user exists before updating
        cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
        existing_user = cursor.fetchone()

        if not existing_user:
            await update.message.reply_text("User not found. Please use /start first to register.")
            return ConversationHandler.END

        # Update wallet address
        cursor.execute('UPDATE users SET wallet_address = ? WHERE user_id = ?', (wallet_address, user_id))
        conn.commit()
        
        await update.message.reply_text(f"✅ Your wallet address has been successfully saved: {wallet_address}")
        return ConversationHandler.END

    except Exception as e:
        await update.message.reply_text("An error occurred while saving your wallet address. Please try again.")
        logging.error(f"Error saving wallet address: {e}")
        return ConversationHandler.END
    
    finally:
        conn.close()

async def cancel_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the wallet settings conversation."""
    await update.message.reply_text("Wallet settings cancelled.")
    return ConversationHandler.END

# Balance command
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user = update.effective_user
    
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT points, wallet_address FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        points, wallet_address = result
        
        # Bitcoin image URL (direct Telegram file URL)
        image_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/4/46/Bitcoin.svg/1200px-Bitcoin.svg.png"
        
        # Prepare the message
        message = (
            "🤖 User Profile & Balance 🤖\n\n"
            f"👤 Name: {user.first_name} {user.last_name or ''}\n"
            f"🆔 User ID: {user_id}\n"
            f"💰 Current Balance: {points} points\n"
            f"💳 Linked Wallet: {wallet_address or 'Not set'}"
        )
        
        # Send message with image
        await update.message.reply_photo(
            photo=image_url, 
            caption=message
        )
    else:
        await update.message.reply_text("User not found. Please /start first.")

# Referral link handler
async def handle_start_referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    referral_code = context.args[0] if context.args else None
    new_user_id = update.effective_user.id
    
    if referral_code:
        conn = sqlite3.connect('user_database.db')
        cursor = conn.cursor()
        
        # Find the referrer
        cursor.execute('SELECT user_id FROM users WHERE referral_code = ?', (referral_code,))
        referrer = cursor.fetchone()
        
        if referrer:
            # Add points to referrer
            referrer_id = referrer[0]
            cursor.execute('''
                UPDATE users 
                SET points = points + 1500 
                WHERE user_id = ?
            ''', (referrer_id,))
            
            # Create new user
            new_referral_code = generate_referral_code(new_user_id)
            cursor.execute('''
                INSERT INTO users (user_id, points, referral_code) 
                VALUES (?, 5000, ?)
            ''', (new_user_id, new_referral_code))
            
            conn.commit()
            conn.close()
            
            await update.message.reply_text(
                f"Welcome! You've been given 5000 points. "
                f"Your referrer received 1500 points. "
                f"Your unique referral link is: https://t.me/test123zekpotbot?start={new_referral_code}"
            )
        else:
            conn.close()
    else:
        await start(update, context)

# Generate unique referral code (previous function remains the same)
def generate_referral_code(user_id):
    return hashlib.sha256(f"referral_{user_id}".encode()).hexdigest()[:8]

# Previous functions (start, settings, balance, handle_wallet, handle_start_referral) remain the same

# About command
async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    about_text = (
        "🚀 Referral Points Bot\n\n"
        "• Start with 5000 free points\n"
        "• Earn 1500 points for each successful referral\n"
        "• Check balance with /balance\n"
        "• Set wallet with /settings\n"
        "• Withdraw points when you have 6500 or more"
    )
    await update.message.reply_text(about_text)

# Withdraw command
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT points, wallet_address FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if not result:
        await update.message.reply_text("User not found. Please /start first.")
        conn.close()
        return
    
    points, wallet_address = result
    
    if points < 6500:
        await update.message.reply_text(f"Insufficient points. You need at least 6500 points. Current balance: {points} points")
        conn.close()
        return
    
    if not wallet_address:
        await update.message.reply_text("Please set your wallet address first using /settings")
        conn.close()
        return
    
    # Confirmation keyboard
    keyboard = [
        [
            InlineKeyboardButton("Confirm Withdrawal", callback_data='confirm_withdraw'),
            InlineKeyboardButton("Cancel", callback_data='cancel_withdraw')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Withdraw {points} points to wallet {wallet_address}?", 
        reply_markup=reply_markup
    )
    
    conn.close()

# Withdrawal confirmation handler
async def handle_withdraw_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    
    # Only handle confirm_withdraw and cancel_withdraw callbacks
    if query.data not in ['confirm_withdraw', 'cancel_withdraw']:
        return
    
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == 'cancel_withdraw':
        await query.edit_message_text("Withdrawal cancelled.")
        return
    
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT points, wallet_address FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if not result or result[0] < 6500:
        await query.edit_message_text("Withdrawal failed. Insufficient points.")
        conn.close()
        return
    
    points, wallet_address = result
    
    # Processing message
    processing_msg = await query.message.reply_text("Processing withdrawal...")
    
    # Simulate withdrawal processing
    progress_msg = await query.message.reply_text("⬜⬜⬜⬜⬜")
    
    for i in range(1, 6):
        await asyncio.sleep(3)
        progress_bar = "🟩" * i + "⬜" * (5 - i)
        await progress_msg.edit_text(progress_bar)
    
    # Update points after processing
    cursor.execute('UPDATE users SET points = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
    
    await processing_msg.delete()
    await progress_msg.delete()
    await query.message.reply_text(f"Withdrawal successful! Transferred {points} points to {wallet_address}")


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


ADMIN_IDS = [5279018187]  
USERS_PER_PAGE = 5


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT admin_id FROM administrators')
    db_admins = {row[0] for row in cursor.fetchall()}
    conn.close()
    
    is_main_admin = user_id in ADMIN_IDS
    is_admin = is_main_admin or user_id in db_admins 

    if not await check_admin(update):
        return  # Exit if not an admin

    # Base admin panel options (visible to all admins)
    keyboard = [
        [InlineKeyboardButton("👥 Manage Users", callback_data='admin_users_0')],
        [InlineKeyboardButton("📋 View Referrals", callback_data='admin_referrals_0')],
        [InlineKeyboardButton("✉️ Messages", callback_data='admin_messages_0')],
        [InlineKeyboardButton("🔇 Muted Users", callback_data='view_muted_users_0')]
    ]

    # Only main admins can manage other admins
    if is_main_admin:
        keyboard.append([InlineKeyboardButton("👮 Manage Admins", callback_data='manage_admins_panel')])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("🔐 Admin Panel\n\nSelect an action:", reply_markup=reply_markup)

async def check_admin(update: Update) -> bool:
    user_id = update.effective_user.id

    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT admin_id FROM administrators')
    db_admins = {row[0] for row in cursor.fetchall()}
    conn.close()

    # Assuming ADMIN_IDS is a predefined set of main admin IDs
    is_main_admin = user_id in ADMIN_IDS
    is_admin = is_main_admin or user_id in db_admins 

    if not is_admin:
        if update.message:  # Check if the message exists
            await update.message.reply_text("⛔ Access denied.")
        return False
    return True

async def show_users_list(query, page: int):
    try:
        admin_id = query.from_user.id
        conn = sqlite3.connect('user_database.db')
        cursor = conn.cursor()
        
        # Get admin's display preference
        cursor.execute('SELECT display_mode FROM admin_settings WHERE admin_id = ?', (admin_id,))
        result = cursor.fetchone()
        display_mode = result[0] if result else 'user_id'
        
        # Get total number of users
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        # Calculate total pages
        total_pages = math.ceil(total_users / USERS_PER_PAGE)
        
        # Get users for current page
        cursor.execute(
            'SELECT user_id, points FROM users LIMIT ? OFFSET ?',
            (USERS_PER_PAGE, page * USERS_PER_PAGE)
        )
        users = cursor.fetchall()
        
        keyboard = []
        for user in users:
            user_id, points = user
            try:
                user_info = await query.bot.get_chat(user_id)
                username = user_info.username
                if username:
                    username = f"@{username}"
                else:
                    username = "No username"
            except:
                username = "Unknown"
            
            display_text = ""
            if display_mode == 'user_id':
                display_text = f"ID: {user_id}"
            elif display_mode == 'nickname':
                display_text = f"ID: {user_id}"
            elif display_mode == 'both':
                display_text = f"ID: {user_id}"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"{display_text} | 💰 {points}",
                    callback_data=f'modify_user_{user_id}'
                ),
                InlineKeyboardButton(
                    "❌ Delete",
                    callback_data=f'delete_user_{user_id}'
                )
            ])
        
        # Add navigation buttons
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'admin_users_{page-1}'))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f'admin_users_{page+1}'))
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data='admin_back')])
        
        await query.edit_message_text(
            f"👥 Users List (Page {page + 1}/{max(1, total_pages)})\nSelect a user to modify:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_users_list: {e}")
        await query.edit_message_text(
            "An error occurred while fetching users list.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='admin_back')]])
        )
    finally:
        conn.close()

async def show_referrals_list(query, page: int):
    try:
        conn = sqlite3.connect('user_database.db')
        cursor = conn.cursor()
        
        # Get users with their referrers
        cursor.execute('''
            SELECT u1.user_id, u1.points, u1.referral_code, u2.user_id as referrer_id 
            FROM users u1 
            LEFT JOIN users u2 ON u1.referred_by = u2.user_id
            LIMIT ? OFFSET ?
        ''', (USERS_PER_PAGE, page * USERS_PER_PAGE))
        referrals = cursor.fetchall()
        
        # Get total count for pagination
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        total_pages = math.ceil(total_users / USERS_PER_PAGE)
        
        message_text = f"📋 Referrals List (Page {page + 1}/{max(1, total_pages)})\n\n"
        for user_id, points, ref_code, referrer_id in referrals:
            message_text += f"👤 User {user_id}\n"
            message_text += f"└ 💰 Points: {points}\n"
            message_text += f"└ 🎫 Code: {ref_code}\n"
            message_text += f"└ 👥 Referred by: {referrer_id or 'None'}\n\n"
        
        keyboard = []
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'admin_referrals_{page-1}'))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f'admin_referrals_{page+1}'))
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data='admin_back')])
        
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_referrals_list: {e}")
        await query.edit_message_text(
            "An error occurred while fetching referrals list.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='admin_back')]])
        )
    finally:
        conn.close()

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        user_id = update.effective_user.id
        
        if not await check_admin(update):
            return  # Exit if not an admin
        
        await query.answer()
        
        # Handle display mode settings
        if query.data.startswith('display_mode_'):
            mode = query.data.split('_')[2]
            conn = sqlite3.connect('user_database.db')
            cursor = conn.cursor()
            
            # Update admin settings
            cursor.execute('''
                INSERT OR REPLACE INTO admin_settings (admin_id, display_mode)
                VALUES (?, ?)
            ''', (user_id, mode))
            conn.commit()
            conn.close()
            
            mode_text = mode.replace('_', ' ').title()
            await query.edit_message_text(
                f"✅ Display mode updated to: {mode_text}\n\n"
                "The new display mode will be used when viewing user lists.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Admin Panel", callback_data='admin_back')
                ]])
            )
            return
            
        # Split the callback data once and reuse it
        data_parts = query.data.split('_')
        
        # Handle other admin callbacks
        if query.data.startswith('admin_users_'):
            page = int(data_parts[2])
            await show_users_list(query, page)
        
        elif query.data.startswith('admin_referrals_'):
            page = int(data_parts[2])
            await show_referrals_list(query, page)
        
        elif query.data.startswith('modify_user_'):
            target_user_id = int(data_parts[2])
            await show_user_actions(query, target_user_id)
        
        elif query.data.startswith('delete_user_'):
            target_user_id = int(data_parts[2])
            await delete_user(query, target_user_id)
        
        elif query.data.startswith('set_points_'):
            target_user_id = int(data_parts[2])
            await show_points_options(query, target_user_id)
        
        elif query.data.startswith('confirm_points_'):
            # Fix: Handle confirm_points with proper parsing
            # Expected format: confirm_points_userid_points
            if len(data_parts) >= 4:  # Make sure we have all parts
                user_id = int(data_parts[2])
                points = int(data_parts[3])
                await modify_user_points(query, user_id, points)
        
        elif query.data.startswith('reset_user_'):
            target_user_id = int(data_parts[2])
            await reset_user(query, target_user_id)
            
        elif query.data.startswith('admin_messages_'):
            page = int(data_parts[2])
            await show_messages(query, page)
            
        elif query.data.startswith('view_muted_users_'):
            page = int(data_parts[3])
            await show_muted_users(query, page)
        
        elif query.data.startswith('view_message_'):
            message_id = int(data_parts[2])
            await view_message(query, message_id)
            
        elif query.data.startswith('mute_user_'):
            if len(data_parts) >= 4:  # Make sure we have all parts
                user_id = int(data_parts[2])
                duration = data_parts[3]
                await handle_user_mute(query, user_id, duration, context)
            
        elif query.data.startswith('unmute_user_'):
            user_id = int(data_parts[2])
            await handle_user_unmute(query, user_id)
            
        elif query.data == 'manage_admins_panel':
            await show_admin_management(query)
            
        elif query.data == 'add_admin':
            context.user_data['awaiting_admin_id'] = True
            await query.edit_message_text(
                "Please send the Telegram ID of the new admin.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data='manage_admins_panel')]])
            )

        elif query.data.startswith('reply_message_'):
            message_id = int(data_parts[2])
            await handle_message_reply(query, message_id, context)
            
        elif query.data.startswith('ignore_message_'):
            message_id = int(data_parts[2])
            await handle_ignored_message(query, message_id)

        elif query.data.startswith('remove_admin_'):
            admin_id = int(data_parts[2])
            await handle_admin_removal(query, admin_id)
        
        elif query.data == 'admin_back':
            keyboard = [
                [InlineKeyboardButton("👥 Manage Users", callback_data='admin_users_0')],
                [InlineKeyboardButton("📋 View Referrals", callback_data='admin_referrals_0')],
                [InlineKeyboardButton("✉️ Messages", callback_data='admin_messages_0')],
                [InlineKeyboardButton("👮 Manage Admins", callback_data='manage_admins_panel')],
                [InlineKeyboardButton("🔇 Muted Users", callback_data='view_muted_users_0')]
            ]
            await query.edit_message_text(
                "🔐 Admin Panel\n\nSelect an action:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
    except Exception as e:
        logger.error(f"Error in handle_admin_callback: {e}")
        await query.edit_message_text(
            "An error occurred while processing your request.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='admin_back')]])
        )

async def delete_user(query, target_user_id: int):
    try:
        conn = sqlite3.connect('user_database.db')
        cursor = conn.cursor()
        
        # Get user's current referral info before deletion
        cursor.execute('SELECT referral_code FROM users WHERE user_id = ?', (target_user_id,))
        user_data = cursor.fetchone()
        
        if user_data:
            old_referral_code = user_data[0]
            
            # Remove any referrals that were made using this user's referral code
            cursor.execute('UPDATE users SET referred_by = NULL WHERE referred_by = ?', (target_user_id,))
            
            # Delete the user from database
            cursor.execute('DELETE FROM users WHERE user_id = ?', (target_user_id,))
            conn.commit()
            
            await query.edit_message_text(
                f"✅ User {target_user_id} has been deleted from the database.\n"
                "They can start fresh with /start command.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Users", callback_data='admin_users_0')
                ]])
            )
            
            # Try to notify the user about deletion
            try:
                await query.bot.send_message(
                    chat_id=target_user_id,
                    text="Your account has been reset by an administrator.\n"
                         "You can start fresh by using the /start command."
                )
            except Exception as e:
                logger.error(f"Could not notify user {target_user_id}: {e}")
        else:
            await query.edit_message_text(
                "User not found in database.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Users", callback_data='admin_users_0')
                ]])
            )
            
    except Exception as e:
        logger.error(f"Error in delete_user: {e}")
        await query.edit_message_text(
            "An error occurred while deleting user.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='admin_users_0')]])
        )
    finally:
        conn.close()

async def show_user_actions(query, target_user_id: int):
    try:
        conn = sqlite3.connect('user_database.db')
        cursor = conn.cursor()
        cursor.execute('SELECT points, wallet_address, referral_code FROM users WHERE user_id = ?', (target_user_id,))
        user_data = cursor.fetchone()
        
        if not user_data:
            await query.edit_message_text(
                "User not found!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='admin_users_0')]])
            )
            return
        
        points, wallet, referral = user_data
        
        keyboard = [
            [InlineKeyboardButton("💰 Set Points", callback_data=f'set_points_{target_user_id}')],
            [InlineKeyboardButton("🔄 Reset User", callback_data=f'reset_user_{target_user_id}')],
            [InlineKeyboardButton("🔙 Back to Users", callback_data='admin_users_0')]
        ]
        
        message_text = (
            f"👤 User ID: {target_user_id}\n"
            f"💰 Points: {points}\n"
            f"💳 Wallet: {wallet or 'Not set'}\n"
            f"🎫 Referral Code: {referral}\n\n"
            "Select an action:"
        )
        
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_user_actions: {e}")
        await query.edit_message_text(
            "An error occurred while fetching user data.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='admin_users_0')]])
        )
    finally:
        conn.close()

async def show_points_options(query, target_user_id: int):
    points_options = [1000, 5000, 10000, 50000, 100000]
    keyboard = []
    
    # Create rows of 2 buttons each
    for i in range(0, len(points_options), 2):
        row = []
        for points in points_options[i:i+2]:
            row.append(InlineKeyboardButton(
                f"{points} points",
                callback_data=f'confirm_points_{target_user_id}_{points}'
            ))
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f'modify_user_{target_user_id}')])
    
    await query.edit_message_text(
        f"Select new points amount for User {target_user_id}:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def modify_user_points(query, target_user_id: int, new_points: int):
    try:
        conn = sqlite3.connect('user_database.db')
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET points = ? WHERE user_id = ?', (new_points, target_user_id))
        conn.commit()
        
        await query.edit_message_text(
            f"✅ Points updated successfully!\n\n"
            f"User ID: {target_user_id}\n"
            f"New Points: {new_points}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Users", callback_data='admin_users_0')
            ]])
        )
        
    except Exception as e:
        logger.error(f"Error in modify_user_points: {e}")
        await query.edit_message_text(
            "An error occurred while updating points.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='admin_users_0')]])
        )
    finally:
        conn.close()

async def reset_user(query, target_user_id: int):
    try:
        conn = sqlite3.connect('user_database.db')
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET points = 5000, wallet_address = NULL WHERE user_id = ?', (target_user_id,))
        conn.commit()
        
        await query.edit_message_text(
            f"✅ User {target_user_id} has been reset!\n"
            "Points set to 5000 and wallet address cleared.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Users", callback_data='admin_users_0')
            ]])
        )
        
    except Exception as e:
        logger.error(f"Error in reset_user: {e}")
        await query.edit_message_text(
            "An error occurred while resetting user data.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='admin_users_0')]])
        )
    finally:
        conn.close()

active_ad_tasks = {}

class Advertisement:
    def __init__(self, name: str, text: str, buttons: List[Dict[str, str]], interval: int):
        self.name = name
        self.text = text
        self.buttons = buttons
        self.interval = interval
        self.last_sent = None

def load_ads() -> List[Advertisement]:
    try:
        with open('advertisements.json', 'r') as f:
            ads_data = json.load(f)
            return [
                Advertisement(
                    ad['name'],
                    ad['text'],
                    ad['buttons'],
                    ad['interval']
                ) for ad in ads_data
            ]
    except FileNotFoundError:
        return []

def save_ads(ads: List[Advertisement]):
    ads_data = [
        {
            'name': ad.name,
            'text': ad.text,
            'buttons': ad.buttons,
            'interval': ad.interval
        } for ad in ads
    ]
    with open('advertisements.json', 'w') as f:
        json.dump(ads_data, f, indent=4)

async def send_advertisement(bot, ad: Advertisement):
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    users = cursor.fetchall()
    conn.close()

    # Create keyboard from buttons
    keyboard = []
    row = []
    for button in ad.buttons:
        row.append(InlineKeyboardButton(
            text=button['text'],
            url=button['url']
        ))
        if len(row) == 2:  # 2 buttons per row
            keyboard.append(row)
            row = []
    if row:  # Add remaining buttons
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    for user in users:
        try:
            await bot.send_message(
                chat_id=user[0],
                text=ad.text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            await asyncio.sleep(0.05)  # Small delay to avoid hitting limits
        except TelegramError:
            continue

async def advertisement_loop(bot, ad: Advertisement):
    while True:
        await send_advertisement(bot, ad)
        await asyncio.sleep(ad.interval)

async def adminadd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return  # Exit if not an admin

    # Start collecting ad information
    await update.message.reply_text(
        "First, send me a name for this advertisement (e.g., 'Summer Promo')\n"
        "Or send /cancel to cancel the process"
    )
    context.user_data['awaiting_ad'] = 'name'

async def handle_ad_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS or 'awaiting_ad' not in context.user_data:
        return

    if update.message.text == '/cancel':
        context.user_data.clear()
        await update.message.reply_text("❌ Advertisement creation cancelled.")
        return

    state = context.user_data['awaiting_ad']
    
    if state == 'name':
        # Check if name already exists
        ads = load_ads()
        if any(ad.name == update.message.text for ad in ads):
            await update.message.reply_text("This name already exists. Please choose a different name.")
            return
            
        context.user_data['ad_name'] = update.message.text
        await update.message.reply_text(
            "Send me the advertisement text. You can use HTML formatting.\n"
            "Example:\n"
            "<b>Bold text</b>\n"
            "<i>Italic text</i>\n"
            "<a href='http://example.com'>Link text</a>\n\n"
            "Or send /cancel to cancel"
        )
        context.user_data['awaiting_ad'] = 'text'

    elif state == 'text':
        context.user_data['ad_text'] = update.message.text
        context.user_data['ad_buttons'] = []
        await update.message.reply_text(
            "Send me the button in format:\n"
            "Button Text | http://example.com\n"
            "Send 'done' when finished adding buttons, 'skip' for no buttons, or /cancel to cancel"
        )
        context.user_data['awaiting_ad'] = 'buttons'

    elif state == 'buttons':
        if update.message.text.lower() in ['done', 'skip']:
            await update.message.reply_text(
                "Send me the interval in seconds between sends (e.g., 3600 for 1 hour)\n"
                "Or send /cancel to cancel"
            )
            context.user_data['awaiting_ad'] = 'interval'
        else:
            try:
                text, url = update.message.text.split('|')
                context.user_data['ad_buttons'].append({
                    'text': text.strip(),
                    'url': url.strip()
                })
                await update.message.reply_text("Button added! Send another or 'done' when finished.")
            except ValueError:
                await update.message.reply_text("Invalid format. Use: Button Text | http://example.com")

    elif state == 'interval':
        try:
            interval = int(update.message.text)
            if interval < 60:
                await update.message.reply_text("Interval must be at least 60 seconds.")
                return

            # Create new advertisement
            ad = Advertisement(
                context.user_data['ad_name'],
                context.user_data['ad_text'],
                context.user_data['ad_buttons'],
                interval
            )

            # Load existing ads
            ads = load_ads()
            ads.append(ad)
            save_ads(ads)

            # Start the advertisement loop
            task = asyncio.create_task(advertisement_loop(context.bot, ad))
            active_ad_tasks[ad.name] = task

            await update.message.reply_text(
                "✅ Advertisement created and scheduled!\n\n"
                f"Name: {ad.name}\n"
                f"Text: {ad.text}\n"
                f"Interval: Every {interval} seconds"
            )
            
            # Clear user data
            context.user_data.clear()

        except ValueError:
            await update.message.reply_text("Please send a valid number of seconds.")

async def admin_ads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return  # Exit if not an admin

    ads = load_ads()
    if not ads:
        await update.message.reply_text("No advertisements found.")
        return

    keyboard = []
    for ad in ads:
        keyboard.append([InlineKeyboardButton(
            f"❌ Remove: {ad.name}",
            callback_data=f'remove_ad_{ad.name}'
        )])
    
    await update.message.reply_text(
        "📢 Active Advertisements\nSelect an ad to remove:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_ad_removal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = update.effective_user.id
    
    if not await check_admin(update):
        return  # Exit if not an admin

    await query.answer()
    
    if query.data.startswith('remove_ad_'):
        ad_name = query.data[10:]  # Remove 'remove_ad_' prefix
        
        # Stop the ad task if it's running
        if ad_name in active_ad_tasks:
            active_ad_tasks[ad_name].cancel()
            del active_ad_tasks[ad_name]
        
        # Remove from saved ads
        ads = load_ads()
        ads = [ad for ad in ads if ad.name != ad_name]
        save_ads(ads)
        
        await query.edit_message_text(f"✅ Advertisement '{ad_name}' has been removed.")

async def start_existing_ads(application):
    ads = load_ads()
    for i, ad in enumerate(ads):
        task = asyncio.create_task(advertisement_loop(application.bot, ad))
        active_ad_tasks[i] = task


# Define states
WAITING_FOR_ADMIN_ID = 1

# Command to manage admins
async def manage_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    # Check if user is main admin
    cursor.execute('SELECT is_main_admin FROM administrators WHERE admin_id = ?', (user_id,))
    result = cursor.fetchone()
    is_main_admin = result[0] if result else False
    
    if not is_main_admin:
        conn.close()
        await update.message.reply_text("Only the main admin can manage other admins.")
        return
    
    # Get current admins
    cursor.execute('SELECT admin_id, is_main_admin FROM administrators')
    admins = cursor.fetchall()
    conn.close()
    
    keyboard = []
    for admin_id, is_main in admins:
        if not is_main:  # Don't show remove button for main admin
            keyboard.append([
                InlineKeyboardButton(
                    f"Remove Admin: {admin_id}",
                    callback_data=f'remove_admin_{admin_id}'
                )
            ])
    
    keyboard.append([InlineKeyboardButton("➕ Add New Admin", callback_data='add_admin')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='admin_back')])
    
    await update.message.reply_text(
        "👮 Admin Management\n\nSelect an action:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def start_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Please enter the Telegram ID of the new admin.\n"
        "You can forward a message from the user to get their ID, or ask them to use /id command.\n\n"
        "Send /cancel to cancel this operation."
    )
    return WAITING_FOR_ADMIN_ID

async def show_admin_management(query):
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    # Get current admins
    cursor.execute('SELECT admin_id, is_main_admin FROM administrators')
    admins = cursor.fetchall()
    
    keyboard = []
    for admin_id, is_main in admins:
        if not is_main:  # Don't show remove button for main admin
            keyboard.append([
                InlineKeyboardButton(
                    f"Remove Admin: {admin_id}",
                    callback_data=f'remove_admin_{admin_id}'
                )
            ])
    
    keyboard.append([InlineKeyboardButton("➕ Add New Admin", callback_data='add_admin')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='admin_back')])
    
    await query.edit_message_text(
        "👮 Admin Management\n\nCurrent Admins:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    conn.close()

async def cancel_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Admin addition cancelled.")
    return ConversationHandler.END



async def handle_admin_removal(query, admin_id: int):
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    # Check if the target is the main admin
    cursor.execute('SELECT is_main_admin FROM administrators WHERE admin_id = ?', (admin_id,))
    result = cursor.fetchone()
    is_main = result[0] if result else False  # Correctly handle fetchone() result
    
    if is_main:
        await query.edit_message_text(
            "Cannot remove main admin.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='manage_admins_panel')]])
        )
        conn.close()  # Ensure the connection is closed
        return
    
    cursor.execute('DELETE FROM administrators WHERE admin_id = ?', (admin_id,))
    conn.commit()
    conn.close()
    
    await query.edit_message_text(
        f"Admin {admin_id} has been removed.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='manage_admins_panel')]])
    )

async def handle_admin_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message_text = update.message.text.strip()

    logger.info(f"📩 Received admin ID input: '{message_text}' from {user_id}")

    if 'awaiting_admin_id' not in context.user_data:
        logger.warning("⚠️ Bot is NOT expecting admin ID input. Ignoring message.")
        return

    try:
        new_admin_id = int(message_text)  # Convert input to an integer
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Please enter a valid numerical ID.")
        return

    # Check if user is already an admin
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT admin_id FROM administrators WHERE admin_id = ?', (new_admin_id,))
    
    if cursor.fetchone():
        await update.message.reply_text("⚠️ This user is already an admin.")
        conn.close()
        return

    # Insert new admin
    cursor.execute('INSERT INTO administrators (admin_id, added_by, added_at) VALUES (?, ?, ?)', 
                   (new_admin_id, user_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    # Notify admin
    await update.message.reply_text(f"✅ User {new_admin_id} has been added as an admin.")

    # Notify new admin
    try:
        await context.bot.send_message(chat_id=new_admin_id, text="🎉 You have been added as an admin!")
    except Exception as e:
        logger.error(f"⚠️ Failed to notify new admin {new_admin_id}: {e}")

    # Clear context state
    context.user_data.pop('awaiting_admin_id', None)

    logger.info(f"✅ Successfully added new admin: {new_admin_id}")

async def cancel_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Admin addition cancelled.")
    return ConversationHandler.END



def is_main_admin(user_id: int) -> bool:
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT is_main_admin FROM administrators WHERE admin_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return bool(result and result[0])

async def message_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    # Check if user is muted
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT muted_until FROM muted_users WHERE user_id = ?', (user_id,))
    muted = cursor.fetchone()
    conn.close()
    
    if muted:
        muted_until = datetime.fromisoformat(muted[0])
        if muted_until > datetime.now():
            await update.message.reply_text("You are currently muted and cannot send messages to admin.")
            return
    
    await update.message.reply_text(
        "Please send your message to the admin (max 300 characters).\n"
        "Note: Messages containing banned words will not be delivered."
    )
    context.user_data['awaiting_admin_message'] = True

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message = update.message.text
    
    # Handle admin reply
    if context.user_data.get('awaiting_reply'):
        message_id = context.user_data['awaiting_reply']
        await save_admin_reply(message_id, message, user_id, context)
        del context.user_data['awaiting_reply']
        await update.message.reply_text(
            "Reply sent successfully!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Messages", callback_data='admin_messages_0')
            ]])
        )
        return
    
    # Handle regular user message to admin
    if context.user_data.get('awaiting_admin_message'):
        # Check message length
        if len(message) > 300:
            await update.message.reply_text("Message too long! Please keep it under 300 characters.")
            return
        
        # Check for banned words
        conn = sqlite3.connect('user_database.db')
        cursor = conn.cursor()
        cursor.execute('SELECT word FROM banned_words')
        banned_words = {row[0].lower() for row in cursor.fetchall()}
        
        message_lower = message.lower()
        if any(word in message_lower for word in banned_words):
            await update.message.reply_text("Message contains banned words and cannot be sent.")
            conn.close()
            return
        
        # Store message
        cursor.execute('''
            INSERT INTO messages (user_id, message)
            VALUES (?, ?)
        ''', (user_id, message))
        conn.commit()
        conn.close()
        
        await update.message.reply_text("Your message has been sent to the admin.")
        context.user_data['awaiting_admin_message'] = False


async def show_messages(query, page: int):
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    # Get total pending messages count
    cursor.execute('SELECT COUNT(*) FROM messages WHERE status = "pending"')
    total_messages = cursor.fetchone()[0]
    total_pages = math.ceil(total_messages / 5)  # 5 messages per page
    
    # Get pending messages for current page
    cursor.execute('''
        SELECT message_id, user_id, message, timestamp 
        FROM messages 
        WHERE status = "pending"
        ORDER BY timestamp DESC
        LIMIT 5 OFFSET ?
    ''', (page * 5,))
    messages = cursor.fetchall()
    
    keyboard = []
    for msg_id, user_id, msg_text, timestamp in messages:
        preview = f"{msg_text[:30]}..." if len(msg_text) > 30 else msg_text
        keyboard.append([
            InlineKeyboardButton(
                f"From {user_id}: {preview}",
                callback_data=f'view_message_{msg_id}'
            )
        ])
    
    # Add navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'admin_messages_{page-1}'))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f'admin_messages_{page+1}'))
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data='admin_back')])
    
    message_text = "📨 Pending Messages"
    if total_messages == 0:
        message_text += "\n\nNo pending messages."
    else:
        message_text += f" (Page {page + 1}/{max(1, total_pages)})"
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    conn.close()


async def view_message(query, message_id: int):
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT user_id, message, timestamp 
        FROM messages 
        WHERE message_id = ?
    ''', (message_id,))
    message = cursor.fetchone()
    conn.close()
    
    if not message:
        await query.edit_message_text(
            "Message not found.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data='admin_messages_0')
            ]])
        )
        return
    
    user_id, msg_text, timestamp = message
    
    # Create mute duration options
    keyboard = [
        [InlineKeyboardButton("✍️ Reply", callback_data=f'reply_message_{message_id}')],
        [InlineKeyboardButton("❌ Ignore", callback_data=f'ignore_message_{message_id}')],
        [InlineKeyboardButton("🔇 Mute 1 Day", callback_data=f'mute_user_{user_id}_1d')],
        [InlineKeyboardButton("🔇 Mute 1 Week", callback_data=f'mute_user_{user_id}_1w')],
        [InlineKeyboardButton("🔇 Mute 2 Weeks", callback_data=f'mute_user_{user_id}_2w')],
        [InlineKeyboardButton("🔇 Mute 1 Month", callback_data=f'mute_user_{user_id}_1m')],
        [InlineKeyboardButton("🔇 Mute Forever", callback_data=f'mute_user_{user_id}_forever')],
        [InlineKeyboardButton("🔙 Back", callback_data='admin_messages_0')]
    ]
    
    await query.edit_message_text(
        f"Message from User {user_id}\n"
        f"Sent at: {timestamp}\n\n"
        f"Message:\n{msg_text}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_user_mute(query, user_id: int, duration: str, context: ContextTypes.DEFAULT_TYPE):
    mute_until = datetime.now()
    
    if duration == '1d':
        mute_until += timedelta(days=1)
    elif duration == '1w':
        mute_until += timedelta(weeks=1)
    elif duration == '2w':
        mute_until += timedelta(weeks=2)
    elif duration == '1m':
        mute_until += timedelta(days=30)
    elif duration == 'forever':
        mute_until = datetime.max
    
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO muted_users (user_id, muted_until, muted_by)
        VALUES (?, ?, ?)
    ''', (user_id, mute_until.isoformat(), query.from_user.id))
    
    conn.commit()
    conn.close()
    
    # Notify user about mute
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"You have been muted until {mute_until.strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except:
        pass
    
    await query.edit_message_text(
        f"User {user_id} has been muted until {mute_until.strftime('%Y-%m-%d %H:%M:%S')}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data='admin_messages_0')
        ]])
    )

# Show muted users
async def show_muted_users(query, page: int):
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM muted_users')
    total_users = cursor.fetchone()[0]
    total_pages = math.ceil(total_users / 5)
    
    cursor.execute('''
        SELECT user_id, muted_until, muted_by 
        FROM muted_users 
        LIMIT 5 OFFSET ?
    ''', (page * 5,))
    muted_users = cursor.fetchall()
    
    keyboard = []
    for user_id, muted_until, muted_by in muted_users:
        muted_until_dt = datetime.fromisoformat(muted_until)
        if muted_until_dt > datetime.now():
            keyboard.append([
                InlineKeyboardButton(
                    f"Unmute User {user_id}",
                    callback_data=f'unmute_user_{user_id}'
                )
            ])
    
    # Add navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'view_muted_users_{page-1}'))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f'view_muted_users_{page+1}'))
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data='admin_back')])
    
    message_text = "🔇 Muted Users:\n\n"
    for user_id, muted_until, muted_by in muted_users:
        muted_until_dt = datetime.fromisoformat(muted_until)
        message_text += f"User {user_id}\n"
        message_text += f"Muted until: {muted_until_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        message_text += f"Muted by: {muted_by}\n\n"
    
    await query.edit_message_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    conn.close()

# Handle user unmuting
async def handle_user_unmute(query, user_id: int):
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM muted_users WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
    
    await query.edit_message_text(
        f"User {user_id} has been unmuted.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data='view_muted_users_0')
        ]])
    )

# Manage banned words
async def manage_banned_words(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    
    command = update.message.text.split()[0].lower()
    if len(context.args) == 0:
        await update.message.reply_text(
            "Please provide a word to ban/unban.\n"
            "Usage: /addword <word> or /removeword <word>"
        )
        return
    
    word = context.args[0].lower()
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    if command == '/addword':
        cursor.execute('INSERT OR IGNORE INTO banned_words (word, added_by) VALUES (?, ?)',
                      (word, update.effective_user.id))
        message = f"Word '{word}' has been banned."
    else:  # /removeword
        cursor.execute('DELETE FROM banned_words WHERE word = ?', (word,))
        message = f"Word '{word}' has been unbanned."
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text(message)


async def handle_message_reply(query, message_id: int, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['awaiting_reply'] = message_id
    await query.edit_message_text(
        "Please type your reply message.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Cancel", callback_data=f'view_message_{message_id}')
        ]])
    )

# New function to save admin reply and notify user
async def save_admin_reply(message_id: int, reply_text: str, admin_id: int, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    # Get user_id and update message status
    cursor.execute('''
        UPDATE messages 
        SET status = 'replied', 
            admin_reply = ?,
            replied_by = ?
        WHERE message_id = ?
        RETURNING user_id
    ''', (reply_text, admin_id, message_id))
    
    result = cursor.fetchone()
    user_id = result[0] if result else None
    
    conn.commit()
    conn.close()
    
    if user_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"Admin reply to your message:\n\n{reply_text}"
            )
        except:
            pass

async def handle_ignored_message(query, message_id: int):
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE messages 
        SET status = 'ignored'
        WHERE message_id = ?
    ''', (message_id,))
    
    conn.commit()
    conn.close()
    
    await query.edit_message_text(
        "Message has been marked as ignored.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data='admin_messages_0')
        ]])
    )


def main():
    # Initialize database
    init_database()
    
    # Create conversation handler for settings
    settings_handler = ConversationHandler(
        entry_points=[CommandHandler("settings", settings)],
        states={
            WAITING_FOR_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wallet)]
        },
        fallbacks=[CommandHandler("cancel", cancel_settings)]
    )
    admin_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_admin, pattern='^add_admin$')],
        states={
            WAITING_FOR_ADMIN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_id_input)
            ]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_admin_add),
            MessageHandler(filters.ALL, lambda u, c: WAITING_FOR_ADMIN_ID)
        ],
        allow_reentry=True
    )

    # Replace 'YOUR_BOT_TOKEN' with your actual bot token
    application = Application.builder().token('8091822623:AAGC5kB9IMlYmslBwBLU-82gLjUxBtQuNWM').build()
    
    # Register handlers in specific order
    application.add_handler(CommandHandler("start", start))
    application.add_handler(settings_handler)  # Add the conversation handler
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CommandHandler("referral", referral_link))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("adminadd", adminadd))
    application.add_handler(CommandHandler("adminads", admin_ads))
    application.add_handler(CommandHandler("messageadmin", message_admin))
    application.add_handler(CommandHandler("addword", manage_banned_words))
    application.add_handler(CommandHandler("removeword", manage_banned_words))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_admin_message
    ))
    
    # Admin callback handlers
    application.add_handler(CallbackQueryHandler(
        handle_admin_callback,
        pattern='^(admin_|modify_user_|set_points_|confirm_points_|reset_user_|delete_user_|display_mode_|view_message_|reply_message_|ignore_message_|mute_user_|view_muted_users_|unmute_user_|manage_admins_panel|add_admin|remove_admin_)'
    ))
    application.add_handler(admin_conv_handler)
    
    # Your other handlers...
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("adminadd", adminadd))
    # ... rest of your handlers ...
    
    # Make sure these general message handlers come AFTER the conversation handler
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_admin_message
    ))

    # Ad removal handler
    application.add_handler(CallbackQueryHandler(handle_ad_removal, pattern='^remove_ad_'))
    
    # Withdrawal confirmation handler - make pattern specific
    application.add_handler(CallbackQueryHandler(handle_withdraw_confirmation, pattern='^(confirm_withdraw|cancel_withdraw)$'))
    
    # Message handlers
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_IDS),
        handle_ad_creation
    ))
    
    # Start existing ads
    application.job_queue.run_once(
        lambda context: asyncio.create_task(start_existing_ads(application)),
        when=0
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_id_input))
        # Add command handlers first
    application.add_handler(CommandHandler("start", start))
    # ... other command handlers ...

    # Add the admin conversation handler here
    application.add_handler(admin_conv_handler)

    # Then add your other message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_message))

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()





import streamlit as st
import sqlite3
import pandas as pd
import os
from datetime import datetime

LOG_FILE = "user_database.log"
DB_FILE = "user_database.db"

# Function to fetch logs
def fetch_logs():
    logs = []
    try:
        with open(LOG_FILE, "r") as f:
            for line in f:
                logs.append(line.strip())
    except FileNotFoundError:
        logs.append("No logs found.")
    return logs

# Function to clear logs
def clear_logs():
    open(LOG_FILE, "w").close()

# Function to fetch users from database
def fetch_users():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM users", conn)
    conn.close()
    return df

# Function to update user points
def update_user_points(user_id, new_points):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET points = ? WHERE user_id = ?", (new_points, user_id))
    conn.commit()
    conn.close()

# Function to delete a user
def delete_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# Streamlit UI
st.title("Admin Panel - Logs & Database")

# Sidebar Navigation
menu = st.sidebar.radio("Navigation", ["📜 Logs", "👥 User Database"])

# Log Viewer
if menu == "📜 Logs":
    st.subheader("📜 Log Viewer")

    logs = fetch_logs()
    st.text_area("Logs:", "\n".join(logs), height=300)

    if st.button("Clear Logs"):
        clear_logs()
        st.success("Logs cleared!")

# User Database Management
elif menu == "👥 User Database":
    st.subheader("👥 User Management")

    users = fetch_users()

    if users.empty:
        st.warning("No users found in the database.")
    else:
        st.dataframe(users)

        user_id = st.number_input("Enter User ID to Modify:", min_value=1, step=1)
        new_points = st.number_input("Enter New Points:", min_value=0, step=500)

        if st.button("Update Points"):
            update_user_points(user_id, new_points)
            st.success(f"Updated User {user_id}'s points to {new_points}")

        if st.button("Delete User"):
            delete_user(user_id)
            st.warning(f"Deleted User {user_id}")

