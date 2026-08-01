from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 18


def q(**kwargs: Any) -> Any:

    customer = utils.get_customer_ds()
    lineitem = utils.get_line_item_ds()
    orders = utils.get_orders_ds()
    big = (
        lineitem.group_by(kt.col("l_orderkey"))
        .agg(kt.col("l_quantity").sum().alias("sum_quantity"))
        .filter(kt.col("sum_quantity") > kt.lit(300))
    )
    return (
        utils.semi_join(orders, big, [("o_orderkey", "l_orderkey")])
        .inner_join(lineitem, [("o_orderkey", "l_orderkey")])
        .inner_join(customer, [("o_custkey", "c_custkey")])
        .group_by([
            kt.col("c_name"), kt.col("o_custkey"), kt.col("o_orderkey"),
            kt.col("o_orderdate"), kt.col("o_totalprice"),
        ])
        .agg(kt.col("l_quantity").sum().alias("col6"))
        .select([
            kt.col("c_name"),
            kt.col("o_custkey").alias("c_custkey"),
            kt.col("o_orderkey"),
            kt.col("o_orderdate").alias("o_orderdat"),
            kt.col("o_totalprice"),
            kt.col("col6"),
        ])
        .sort(["o_totalprice", "o_orderdat"], descending=[True, False])
        .limit(100)
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
