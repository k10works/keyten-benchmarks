from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 4


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    orders = utils.get_orders_ds()
    var1 = utils.date(1993, 7, 1)
    var2 = utils.date(1993, 10, 1)
    late = lineitem.filter(kt.col("l_commitdate") < kt.col("l_receiptdate"))
    return (
        utils.semi_join(orders, late, [("o_orderkey", "l_orderkey")])
        .filter((kt.col("o_orderdate") >= var1) & (kt.col("o_orderdate") < var2))
        .group_by(kt.col("o_orderpriority"))
        .agg(kt.lit(1).count().alias("order_count"))
        .sort("o_orderpriority")
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
