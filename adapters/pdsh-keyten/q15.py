from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 15


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    supplier = utils.get_supplier_ds()
    var1 = utils.date(1996, 1, 1)
    var2 = utils.date(1996, 4, 1)
    revenue = (
        lineitem.filter((kt.col("l_shipdate") >= var1) & (kt.col("l_shipdate") < var2))
        .group_by(kt.col("l_suppkey"))
        .agg((kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))).sum().alias("total_revenue"))
        .collect()
    )
    best = max(v for v in revenue.column("total_revenue").to_list() if v is not None)
    return (
        supplier.inner_join(revenue.lazy(), [("s_suppkey", "l_suppkey")])
        .filter(kt.col("total_revenue") >= kt.lit(best))
        .select(["s_suppkey", "s_name", "s_address", "s_phone", "total_revenue"])
        .sort("s_suppkey")
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
