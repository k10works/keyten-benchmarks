from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 17


def q(**kwargs: Any) -> Any:

    lineitem = utils.get_line_item_ds()
    part = utils.get_part_ds()
    q1 = (
        part.filter(kt.col("p_brand") == kt.lit("Brand#23"))
        .filter(kt.col("p_container") == kt.lit("MED BOX"))
        .inner_join(lineitem, [("p_partkey", "l_partkey")])
    )
    return (
        q1.group_by(kt.col("p_partkey"))
        .agg(kt.col("l_quantity").mean().alias("avg_quantity"))
        .with_columns((kt.col("avg_quantity") * kt.lit(0.2)).alias("avg_quantity"))
        .inner_join(q1, [("p_partkey", "p_partkey")])
        .filter(kt.col("l_quantity") < kt.col("avg_quantity"))
        .select((kt.col("l_extendedprice").sum() / kt.lit(7.0)).alias("avg_yearly"))
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
