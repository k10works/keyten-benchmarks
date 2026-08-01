from typing import Any

import keyten as kt

from queries.keyten import utils

Q_NUM = 16


def q(**kwargs: Any) -> Any:

    part = utils.get_part_ds()
    partsupp = utils.get_part_supp_ds()
    supplier = utils.get_supplier_ds()
    bad_supp = (
        supplier.filter(utils.matches(kt.col("s_comment"), ".*Customer.*Complaints.*"))
        .select(kt.col("s_suppkey"))
    )
    return (
        part.inner_join(partsupp, [("p_partkey", "ps_partkey")])
        .filter(kt.col("p_brand") != kt.lit("Brand#45"))
        .filter(~utils.matches(kt.col("p_type"), "MEDIUM POLISHED*"))
        .filter(kt.col("p_size").is_in([49, 14, 23, 45, 19, 3, 36, 9]))
        .anti_join(bad_supp, [("ps_suppkey", "s_suppkey")])
        .group_by([kt.col("p_brand"), kt.col("p_type"), kt.col("p_size")])
        .agg(kt.col("ps_suppkey").n_unique().alias("supplier_cnt"))
        .sort(
            ["supplier_cnt", "p_brand", "p_type", "p_size"],
            descending=[True, False, False, False],
        )
    )


if __name__ == "__main__":
    utils.run_query(Q_NUM, lambda: q().collect())
