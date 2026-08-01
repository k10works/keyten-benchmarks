from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 11


def q(**kwargs: Any) -> Any:

    nation = utils.get_nation_ds()
    partsupp = utils.get_part_supp_ds()
    supplier = utils.get_supplier_ds()
    var2 = 0.0001 / utils.settings.scale_factor
    q1 = (
        partsupp.inner_join(supplier, [("ps_suppkey", "s_suppkey")])
        .inner_join(nation, [("s_nationkey", "n_nationkey")])
        .filter(kt.col("n_name") == kt.lit("GERMANY"))
        .with_columns((kt.col("ps_supplycost") * kt.col("ps_availqty")).alias("v"))
        .select([kt.col("ps_partkey"), kt.col("v")])
        .collect()
    )
    threshold = sum(v for v in q1.column("v").to_list() if v is not None) * var2
    return (
        q1.lazy().group_by(kt.col("ps_partkey"))
        .agg(kt.col("v").sum().alias("value"))
        .filter(kt.col("value") > kt.lit(threshold))
        .select([kt.col("ps_partkey"), kt.col("value")])
        .sort("value", descending=True)
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
