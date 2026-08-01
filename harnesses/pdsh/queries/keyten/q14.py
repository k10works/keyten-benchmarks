from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 14


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    part = utils.get_part_ds()
    var1 = utils.date(1995, 9, 1)
    var2 = utils.date(1995, 10, 1)
    disc = kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))
    return (
        lineitem.inner_join(part, [("l_partkey", "p_partkey")])
        .filter((kt.col("l_shipdate") >= var1) & (kt.col("l_shipdate") < var2))
        .with_columns([
            kt.if_else(utils.starts_with(kt.col("p_type"), "PROMO"), disc, kt.lit(0.0)).alias("promo"),
            disc.alias("full"),
        ])
        .select(
            (kt.lit(100.0) * kt.col("promo").sum() / kt.col("full").sum()).alias("promo_revenue")
        )
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
