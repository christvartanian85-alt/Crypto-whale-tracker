import asyncio
import logging

from storage import (
    load_vips,
    load_users,
    load_seen,
    save_seen,
    get_wallet_name,
    get_custom_wallets,
)

from apis import (
    get_txs,
    get_dex,
    get_bubble,
    get_rug,
    get_gmgn,
    get_solana_wallet_funder,
)

from signals import (
    add_to_bundle,
    get_bundle_wallets,
    can_signal,
    build_individual_msg,
    build_signal_msg,
    record_funding,
)

logger = logging.getLogger(__name__)


def extract_token(tx, chain):
    if chain == "solana":
        transfers = tx.get("tokenTransfers", [])

        if transfers:
            token = transfers[0].get("token", {}).get("address")

            if token:
                return token

        inner = tx.get("innerInstructions", [])

        for inst in inner:
            for parsed in inst.get("parsedInstructions", []):
                mint = parsed.get("params", {}).get("mint")

                if mint:
                    return mint

    else:
        contract = tx.get("contractAddress", "")

        if (
            contract
            and contract != "0x0000000000000000000000000000000000000000"
        ):
            return contract

    return None


async def process_wallet(app, wallet, seen_txs, users):
    address = wallet.get("address")

    chain = wallet.get("chain", "solana")

    if not address:
        return

    try:
        txs = await get_txs(chain, address)

    except Exception as e:
        logger.error(
            "get_txs error for %s: %s",
            address,
            e
        )
        return

    for tx in txs[:10]:
        sig = tx.get("txHash") or tx.get("hash")

        if not sig:
            continue

        if sig in seen_txs:
            continue

        seen_txs.add(sig)

        token = extract_token(tx, chain)

        if not token:
            continue

        # Solana funding detection
        if chain == "solana":
            try:
                funder = await get_solana_wallet_funder(address)

                if funder:
                    record_funding(address, funder)

            except Exception as e:
                logger.warning(
                    "Funding lookup failed for %s: %s",
                    address,
                    e
                )

        # Individual alert
        try:
            dex = await get_dex(token)

            msg = build_individual_msg(
                address,
                chain,
                token,
                dex
            )

            for uid in list(users.keys()):
                try:
                    await app.bot.send_message(
                        int(uid),
                        msg
                    )

                except Exception as e:
                    logger.warning(
                        "Send individual error uid=%s: %s",
                        uid,
                        e
                    )

        except Exception as e:
            logger.error(
                "Individual alert error: %s",
                e
            )

        # Bundle tracking
        add_to_bundle(token, address)

        bundle_wallets = get_bundle_wallets(token)

        # Send signal if 2+ wallets bought
        if len(bundle_wallets) >= 2 and can_signal(token):
            try:
                wallet_names = [
                    get_wallet_name(a)
                    for a in bundle_wallets
                ]

                dex = await get_dex(token)

                rug = await get_rug(token)

                bubble = await get_bubble(
                    token,
                    chain
                )

                gmgn = (
                    await get_gmgn(token)
                    if chain == "solana"
                    else None
                )

                msg = build_signal_msg(
                    token=token,
                    chain=chain,
                    buyers=bundle_wallets,
                    wallet_names=wallet_names,
                    dex=dex,
                    rug=rug,
                    bubble=bubble,
                    gmgn=gmgn,
                )

                for uid in list(users.keys()):
                    try:
                        await app.bot.send_message(
                            int(uid),
                            msg
                        )

                    except Exception as e:
                        logger.warning(
                            "Send signal error uid=%s: %s",
                            uid,
                            e
                        )

            except Exception as e:
                logger.error(
                    "Signal build error: %s",
                    e
                )


async def run_tracker(app):
    vips = load_vips()

    users = load_users()

    seen_txs = load_seen()

    # Start with VIP wallets
    all_wallets = list(vips)

    # Add PRO custom wallets
    for uid, udata in users.items():
        if udata.get("plan") == "pro":
            custom = get_custom_wallets(uid)

            all_wallets.extend(custom)

    # Remove duplicate wallets
    unique_wallets = []

    seen_addresses = set()

    for wallet in all_wallets:
        addr = wallet.get("address")

        if not addr:
            continue

        key = (
            wallet.get("chain", "solana"),
            addr
        )

        if key not in seen_addresses:
            seen_addresses.add(key)
            unique_wallets.append(wallet)

    # Parallel batches
    batch_size = 10

    for i in range(0, len(unique_wallets), batch_size):
        batch = unique_wallets[i:i + batch_size]

        tasks = [
            process_wallet(
                app,
                wallet,
                seen_txs,
                users
            )
            for wallet in batch
        ]

        await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        await asyncio.sleep(0.5)

    save_seen(seen_txs)

    logger.info(
        "Tracker run complete. Wallets checked: %d",
        len(unique_wallets)
    )
