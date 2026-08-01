from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 13


def q(**kwargs: Any) -> Any:

    customer = utils.get_customer_ds()
    orders = utils.get_orders_ds()
    orders = orders.filter(~utils.matches(kt.col("o_comment"), "special.*requests"))
    return (
        customer.left_join(orders, [("c_custkey", "o_custkey")])
        .group_by(kt.col("c_custkey"))
        .agg(kt.col("o_orderkey").count().alias("c_count"))
        .group_by(kt.col("c_count"))
        .agg(kt.lit(1).count().alias("custdist"))
        .select([kt.col("c_count"), kt.col("custdist")])
        .sort(["custdist", "c_count"], descending=[True, True])
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
