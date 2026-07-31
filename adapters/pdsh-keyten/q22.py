from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 22


def q(**kwargs: Any) -> Any:

    customer = utils.get_customer_ds()
    orders = utils.get_orders_ds()
    q1 = (
        customer.with_columns(kt.col("c_phone").str_extract("^..").alias("cntrycode"))
        .filter(kt.col("cntrycode").is_in(["13", "31", "23", "29", "30", "18", "17"]))
        .select(["c_acctbal", "c_custkey", "cntrycode"])
        .collect()
    )
    bals = [v for v in q1.column("c_acctbal").to_list() if v is not None and v > 0.0]
    avg_bal = sum(bals) / len(bals)
    q1 = q1.lazy()
    return (
        q1.anti_join(orders, [("c_custkey", "o_custkey")])
        .filter(kt.col("c_acctbal") > kt.lit(avg_bal))
        .group_by(kt.col("cntrycode"))
        .agg([
            kt.col("c_acctbal").count().alias("numcust"),
            kt.col("c_acctbal").sum().alias("totacctbal"),
        ])
        .sort("cntrycode")
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
