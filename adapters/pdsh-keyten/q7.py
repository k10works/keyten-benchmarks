from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 7


def q(**kwargs: Any) -> Any:

    customer = utils.get_customer_ds()
    lineitem = utils.get_line_item_ds()
    nation = utils.get_nation_ds()
    orders = utils.get_orders_ds()
    supplier = utils.get_supplier_ds()
    var1, var2 = "FRANCE", "GERMANY"
    var3 = utils.date(1995, 1, 1)
    var4 = utils.date(1996, 12, 31)
    cust_n = nation.filter(kt.col("n_name").is_in([var1, var2])).select([
        kt.col("n_nationkey"), kt.col("n_name").alias("cust_nation")])
    supp_n = nation.filter(kt.col("n_name").is_in([var1, var2])).select([
        kt.col("n_nationkey"), kt.col("n_name").alias("supp_nation")])
    return (
        customer.inner_join(cust_n, [("c_nationkey", "n_nationkey")])
        .inner_join(orders, [("c_custkey", "o_custkey")])
        .inner_join(lineitem, [("o_orderkey", "l_orderkey")])
        .inner_join(supplier, [("l_suppkey", "s_suppkey")])
        .inner_join(supp_n, [("s_nationkey", "n_nationkey")])
        .filter(
            ((kt.col("cust_nation") == kt.lit(var1)) & (kt.col("supp_nation") == kt.lit(var2)))
            | ((kt.col("cust_nation") == kt.lit(var2)) & (kt.col("supp_nation") == kt.lit(var1)))
        )
        .filter((kt.col("l_shipdate") >= var3) & (kt.col("l_shipdate") <= var4))
        .with_columns([
            (kt.col("l_extendedprice") * (kt.lit(1.0) - kt.col("l_discount"))).alias("volume"),
            kt.col("l_shipdate").year().alias("l_year"),
        ])
        .group_by([kt.col("supp_nation"), kt.col("cust_nation"), kt.col("l_year")])
        .agg(kt.col("volume").sum().alias("revenue"))
        .sort(["supp_nation", "cust_nation", "l_year"])
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
