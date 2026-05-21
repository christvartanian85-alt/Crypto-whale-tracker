import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from config import TOKEN, ADMIN_ID, PAYMENT_WALLET, PRO_PRICE

from storage import (
    load_vips,
    load_users,
    save_users,
    add_custom_wallet,
    get_custom_wallets,
    shorten,
)

from signals import (
    bundle_map,
    get_bundle_wallets,
    get_wallet_name,
)

from tracker import run_tracker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = load_users()
    uid = str(update.effective_user.id)

    if uid not in users:
        users[uid] = {"plan": "free"}
        save_users(users)

    vips = load_vips()

    msg = (
        "🚀 Welcome to Sierastracking Bot!\n\n"
        f"👁 Automatically tracking {len(vips)} whale wallets.\n\n"
        "You will receive:\n"
        "• 💼 Individual alert for every whale buy\n"
        "• 🚨 Red signal when 2+ wallets buy same token\n"
        "• 🔍 Full safety analysis (bundle, fresh wallets, rug)\n\n"
        "Commands:\n"
        "/status - Bot status\n"
        "/topwallets - View tracked wallets\n"
        "/signals - Recent signals\n"
        "/upgrade - PRO plan\n"
        "/help - All commands"
    )

    await update.message.reply_text(msg)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vips = load_vips()
    users = load_users()

    uid = str(update.effective_user.id)

    plan = users.get(uid, {}).get("plan", "free").upper()

    custom = get_custom_wallets(uid)

    msg = (
        "📊 Bot Status\n\n"
        "✅ Status: ONLINE\n"
        f"🐋 VIP Wallets: {len(vips)}\n"
        f"👥 Total Users: {len(users)}\n"
        "⏱ Check Interval: 2 minutes\n"
        f"🚨 Active Bundles: {len(bundle_map)}\n\n"
        f"👤 Your Plan: {plan}\n"
        f"📌 Your Custom Wallets: {len(custom)}"
    )

    await update.message.reply_text(msg)


async def topwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallets = load_vips()

    msg = f"🐋 Tracked VIP Wallets ({len(wallets)} total)\n\n"

    for i, w in enumerate(wallets[:30], 1):
        name = w.get("name") or f"Wallet {i}"
        addr = w.get("address", "")
        chain = w.get("chain", "sol").upper()

        msg += f"{i}. {name} [{chain}] {shorten(addr)}\n"

    if len(wallets) > 30:
        msg += f"\n…and {len(wallets) - 30} more wallets"

    await update.message.reply_text(msg)


async def signals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = {
        k: v
        for k, v in bundle_map.items()
        if len(set(x["wallet"] for x in v)) >= 2
    }

    if not active:
        await update.message.reply_text(
            "📡 No signals yet.\n\n"
            "Signals fire when 2+ tracked wallets buy the same token within 30 minutes."
        )
        return

    msg = "🚨 Recent Signals:\n\n"

    for token, buyers in list(active.items())[:5]:
        wallets = list(set(x["wallet"] for x in buyers))
        names = [get_wallet_name(a) for a in wallets]

        msg += f"🔴 {shorten(token)} - {len(wallets)} wallets\n"

        for n in names[:3]:
            msg += f"  • {n}\n"

        msg += f"  https://dexscreener.com/solana/{token}\n\n"

    await update.message.reply_text(msg)


async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"💎 PRO Plan - {PRO_PRICE} USDT\n\n"
        "✅ Add unlimited custom wallets\n"
        "✅ Your wallets tracked 24/7\n"
        "✅ All VIP wallets (free for everyone)\n\n"
        "💳 Payment (TRC20 USDT):\n"
        f"{PAYMENT_WALLET}\n\n"
        "After payment:\n"
        "/verify_payment YOUR_TX_HASH"
    )

    await update.message.reply_text(msg)


async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: /verify_payment YOUR_TX_HASH"
        )
        return

    tx = context.args[0]

    uid = str(update.effective_user.id)

    users = load_users()

    if uid not in users:
        users[uid] = {"plan": "free"}

    users[uid]["pending_tx"] = tx

    save_users(users)

    await update.message.reply_text(
        f"✅ Payment submitted!\nTX: {tx}\n\n"
        "Admin will verify within 24h."
    )

    if ADMIN_ID:
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"💰 NEW PAYMENT\nUser: {uid}\nTX: {tx}"
            )

        except Exception as e:
            logger.error("Admin notify error: %s", e)


async def grant_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not authorized.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /grant_pro USER_ID"
        )
        return

    target = context.args[0]

    users = load_users()

    if target not in users:
        users[target] = {}

    users[target]["plan"] = "pro"

    save_users(users)

    await update.message.reply_text(
        f"✅ PRO activated for {target}"
    )

    try:
        await context.bot.send_message(
            int(target),
            "🎉 Your PRO plan is now active!\n"
            "You can now add custom wallets with /watch"
        )

    except Exception as e:
        logger.warning("Notify user error: %s", e)


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    users = load_users()

    plan = users.get(uid, {}).get("plan", "free")

    if plan != "pro":
        await update.message.reply_text(
            "🔒 Custom wallet tracking requires PRO plan.\n\n"
            "Type /upgrade to learn more."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "/watch SOL_ADDRESS\n"
            "/watch eth 0xADDRESS\n"
            "/watch bsc 0xADDRESS\n\n"
            "Examples:\n"
            "/watch 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM\n"
            "/watch eth 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045\n"
            "/watch bsc 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
        )
        return

    # /watch eth 0x...
    # /watch bsc 0x...
    # /watch ADDRESS

    if len(context.args) == 2:
        chain_arg = context.args[0].lower()
        address = context.args[1].strip()

        chain_map = {
            "sol": "solana",
            "solana": "solana",
            "eth": "eth",
            "bsc": "bsc",
        }

        chain = chain_map.get(chain_arg, "solana")

    else:
        address = context.args[0].strip()

        chain = "solana"

        if address.startswith("0x"):
            chain = "eth"

    added = add_custom_wallet(uid, address, chain)

    if added:
        await update.message.reply_text(
            f"✅ Now tracking: {shorten(address)}\n"
            f"Chain: {chain.upper()}\n\n"
            "You'll receive alerts when this wallet buys tokens."
        )

    else:
        await update.message.reply_text(
            "This wallet is already being tracked."
        )


async def mywallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    custom = get_custom_wallets(uid)

    if not custom:
        await update.message.reply_text(
            "You have no custom wallets.\n\n"
            "PRO users can add wallets with /watch ADDRESS"
        )
        return

    msg = "📌 Your Custom Wallets:\n\n"

    for i, w in enumerate(custom, 1):
        msg += (
            f"{i}. "
            f"{shorten(w['address'])} "
            f"[{w.get('chain', 'sol').upper()}]\n"
        )

    await update.message.reply_text(msg)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📚 All Commands:\n\n"
        "/start - Start bot\n"
        "/status - Bot & account status\n"
        "/topwallets - View VIP tracked wallets\n"
        "/signals - Active buy signals\n"
        "/mywallets - Your custom wallets\n"
        "/watch ADDRESS - Track custom wallet (PRO)\n"
        "/upgrade - PRO plan info\n"
        "/verify_payment TX - Submit payment proof\n"
        "/help - This message\n\n"
        f"💎 PRO Plan: {PRO_PRICE} USDT\n"
        "✅ Add unlimited custom wallets"
    )

    await update.message.reply_text(msg)


async def job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await run_tracker(context.application)

    except Exception as e:
        logger.error("Tracker job error: %s", e)


def main():
    if not TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("topwallets", topwallets))
    app.add_handler(CommandHandler("signals", signals_cmd))
    app.add_handler(CommandHandler("upgrade", upgrade))
    app.add_handler(CommandHandler("verify_payment", verify_payment))
    app.add_handler(CommandHandler("grant_pro", grant_pro))
    app.add_handler(CommandHandler("watch", watch))
    app.add_handler(CommandHandler("mywallets", mywallets))
    app.add_handler(CommandHandler("help", help_cmd))

    app.job_queue.run_repeating(
        job,
        interval=120,
        first=15
    )

    logger.info("Bot started.")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
