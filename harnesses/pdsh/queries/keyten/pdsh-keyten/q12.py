from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 12


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    orders = utils.get_orders_ds()
    var3 = utils.date(1994, 1, 1)
    var4 = utils.date(1995, 1, 1)
    return (
        orders.inner_join(lineitem, [("o_orderkey", "l_orderkey")])
        .filter(kt.col("l_shipmode").is_in(["MAIL", "SHIP"]))
        .filter(kt.col("l_commitdate") < kt.col("l_receiptdate"))
        .filter(kt.col("l_shipdate") < kt.col("l_commitdate"))
        .filter((kt.col("l_receiptdate") >= var3) & (kt.col("l_receiptdate") < var4))
        .with_columns([
            kt.if_else(kt.col("o_orderpriority").is_in(["1-URGENT", "2-HIGH"]), kt.lit(1), kt.lit(0)).alias("high"),
            kt.if_else(kt.col("o_orderpriority").is_in(["1-URGENT", "2-HIGH"]), kt.lit(0), kt.lit(1)).alias("low"),
        ])
        .group_by(kt.col("l_shipmode"))
        .agg([
            kt.col("high").sum().alias("high_line_count"),
            kt.col("low").sum().alias("low_line_count"),
        ])
        .sort("l_shipmode")
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
