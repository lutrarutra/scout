### Custom functions for SC RNA-seq data

import threading

import gseapy
import numpy as np
import pandas as pd
import scanpy as sc
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler


def scale_log_center(adata, target_sum=None):
    adata.layers["counts"] = adata.X.copy()
    sc.pp.normalize_total(adata, target_sum=target_sum)
    adata.layers["ncounts"] = adata.X.copy()
    sc.pp.log1p(adata)
    adata.layers["centered"] = np.asarray(
        adata.layers["counts"] - adata.layers["counts"].mean(axis=0)
    )
    adata.layers["logcentered"] = np.asarray(adata.X - adata.X.mean(axis=0))


def _rank_group(adata, rank_res, groupby, idx, ref_name, eps=None):
    mapping = {}
    for gene in adata.var_names:
        mapping[gene] = {"z-score": 0.0, "pvals_adj": 0.0, "logFC": 0.0}

    for genes, scores, pvals, logFC in list(
        zip(
            rank_res["names"],
            rank_res["scores"],
            rank_res["pvals_adj"],
            rank_res["logfoldchanges"],
        )
    ):
        mapping[genes[idx]]["z-score"] = scores[idx]
        mapping[genes[idx]]["pvals_adj"] = pvals[idx]
        mapping[genes[idx]]["logFC"] = logFC[idx]

    df = pd.DataFrame(mapping).T

    if eps is None:
        eps = np.nanmin(df["pvals_adj"].values[df["pvals_adj"].values != 0]) * 0.1

    df["-log_pvals_adj"] = (-np.log10(df["pvals_adj"])).clip(
        lower=None, upper=-np.log10(eps)
    )
    df["significant"] = df["pvals_adj"] < 0.05
    df["mu_expression"] = np.asarray(
        adata[adata.obs[groupby] == ref_name, df.index].layers["counts"].mean(axis=0)
    ).flatten()
    df["log_mu_expression"] = np.asarray(
        np.log1p(adata[:, df.index].layers["counts"]).mean(0)
    ).flatten()
    df["dropout"] = (
        1.0
        - np.asarray(
            (
                adata[adata.obs[groupby] == ref_name, df.index].layers["counts"] == 0
            ).mean(0)
        ).flatten()
    )
    df["gene_score"] = (
        df["logFC"]
        * df["-log_pvals_adj"]
        * df["log_mu_expression"]
        * (1.0 - df["dropout"])
    )
    df["abs_score"] = np.abs(df["gene_score"])

    df.index.name = ref_name + "_vs_rest"
    return df


def rank_marker_genes(adata, groupby, method="t-test", eps=None):
    rank_res = sc.tl.rank_genes_groups(
        adata, groupby=groupby, method=method, copy=True
    ).uns["rank_genes_groups"]

    adata.uns[f"rank_genes_{groupby}"] = {}

    for i, ref in enumerate(adata.obs[groupby].unique()):
        adata.uns["rank_genes_" + groupby][ref] = _rank_group(
            adata, rank_res, groupby, i, ref, eps
        )


def GSEA(
    df,
    score_of_interest="gene_score",
    gene_set="KEGG_2021_Human",
    n_threads=None,
    seed=0,
):
    if n_threads is None:
        n_threads = threading.active_count()

    res = gseapy.prerank(
        rnk=df[score_of_interest],
        gene_sets=gene_set,
        no_plot=True,
        processes=n_threads,
        seed=seed,
    ).res2d
    temp = res["Tag %"].str.split("/")
    res["matched_size"] = temp.str[0].astype(int)
    res["geneset_size"] = temp.str[1].astype(int)
    # TODO: get all genes inside the set:
    # res["genes"] = res["genes"].str.split(";")
    res["lead_genes"] = res["Lead_genes"].str.split(";")
    res = res.rename(
        columns={
            "FDR q-val": "fdr",
            "NOM p-val": "pval",
            "NES": "nes",
            "FWER p-val": "fwer",
            "ES": "es",
        }
    )
    res["fdr"] = res["fdr"].astype(float)
    res["pval"] = res["pval"].astype(float)
    res["nes"] = res["nes"].astype(float)
    res["fwer"] = res["fwer"].astype(float)
    res["es"] = res["es"].astype(float)

    res = res.drop(columns=["Lead_genes", "Tag %", "Name"])

    # sg = []
    # for i in range(res.shape[0]):
    #     sg.append([])
    #     for gene in res.iloc[i]["lead_genes"]:
    #         if df.loc[gene, "pvals_adj"] < 0.05:
    #             sg[i].append((gene, df.loc[gene, "pvals_adj"]))

    #     sg[i] = sorted(sg[i], key=lambda tup: tup[1])
    #     sg[i] = [x[0] for x in sg[i]]
    # res["significant_genes"] = sg
    # res["significant_size"] = res["significant_genes"].apply(len)
    # res["significant_fraction"] = res["significant_size"] / res["geneset_size"]

    res["matched_fraction"] = res["matched_size"] / res["geneset_size"]
    res["-log10_fdr"] = -np.log10(res["fdr"])
    res["-log10_fdr"] = res["-log10_fdr"].clip(lower=0, upper=res["-log10_fdr"])

    return res.sort_values("-log10_fdr", ascending=False)


############################################

# OLD STUFF

############################################


def to_df(adata: sc.AnnData, index_col=None) -> None:
    if index_col:
        return pd.DataFrame(
            adata.X, index=adata.obs[index_col], columns=adata.var.index
        )
    return pd.DataFrame(adata.X, index=adata.obs.index, columns=adata.var.index)


def to_csv(adata: sc.AnnData, filepath, sep=",", genes_in_cols=True) -> None:
    if genes_in_cols:
        to_df(adata).to_csv(filepath, sep="\t")
    else:
        to_df(adata).T.to_csv(filepath, sep="\t")


def annotate(
    adata: sc.AnnData,
    annotation_path: str,
    annotation_cols: list,
    barcode_col=0,
    header="infer",
) -> None:
    """
    Annotates adata object given path to csv/tsv with annotation data,
    where samples (cells) are rows, and features (annotation) are columns.

    :param AnnData adata: sc RNA-seq data from Scanpy
    :param str annotation_path: path to csv/tsv file with annotation data
    :param [str] annotation_cols: iteratable indicating which columns to add to annotation
    :param int barcode_col: index of column with cell barcode
    :param bool header: set to 'None' if file does not contain column names

    :rtype: None
    """

    sep = "," if annotation_path.split(".")[-1] == "csv" else "\t"

    if type(barcode_col) == int:
        annotation = pd.read_csv(
            annotation_path, sep=sep, index_col=barcode_col, header=header
        )

    annotation.index = annotation.index.str.replace("-", ".", regex=False)

    if type(annotation_cols) == str:
        annotation_cols = [annotation_cols]

    mapping = annotation.to_dict()

    for col in annotation_cols:
        adata.obs[col] = adata.obs_names.map(mapping[col])


def pseudo_bulk(adata: sc.AnnData, sample_col=None) -> sc.AnnData:
    """
    Creates 'pseudo' bulk from sc RNA-seq data in 'adata' by summing gene counts in each sample

    :param AnnData adata: sc RNA-seq data from Scanpy
    :param str sample_col: name of column in 'adata.obs' which to use to group samples

    :rtype: sc.AnnData
    """
    if sample_col:
        samples = adata.obs[sample_col].unique()
        pseudo = pd.DataFrame(index=adata.var.index, columns=samples)
        for sample in samples:
            pseudo[sample] = adata[adata.obs[sample_col] == sample].X.sum(axis=0)
    else:
        pseudo = pd.DataFrame(index=adata.var.index, columns=[0])
        pseudo[0] = adata.X.sum(axis=0)

    pseudo_adata = sc.AnnData(pseudo.transpose())
    # pseudo_adata.obs["sample"] = pseudo_adata.obs.index
    # pseudo_adata.obs = pseudo_adata.obs.reset_index(drop=True)

    return pseudo_adata


def pseudo_bulk_df(adata, sample_col=None) -> pd.DataFrame:
    """
    Creates 'pseudo' bulk from sc RNA-seq data in 'adata' by summing gene counts in each sample

    :param AnnData adata: sc RNA-seq data from Scanpy
    :param str sample_col: name of column in 'adata.obs' which to use to group samples

    :rtype: pd.DataFrame
    """
    if sample_col:
        samples = adata.obs[sample_col].unique()
        pseudo = pd.DataFrame(index=adata.var.index, columns=samples)
        for sample in samples:
            pseudo[sample] = adata[adata.obs[sample_col] == sample].X.sum(axis=0)
    else:
        pseudo = pd.DataFrame(index=adata.var.index, columns=[0])
        pseudo[0] = adata.X.sum(axis=0)

    pseudo.index.name = "GeneSymbol"

    return pseudo.transpose()


def calculate_proportions(adata, groupby, clusterby):
    portions = pd.DataFrame(
        index=adata.obs[groupby].cat.categories,
        columns=adata.obs[clusterby].cat.categories,
    )
    for group in adata.obs[groupby].cat.categories:
        group_adata = adata[(adata.obs[groupby] == group)]
        for cluster in adata.obs[clusterby].cat.categories:
            portions.loc[group, cluster] = (
                group_adata[group_adata.obs[clusterby] == cluster].obs.shape[0]
                / group_adata.obs.shape[0]
            )

    return portions


def signature_matrix(adata, groupby):
    sc.tl.rank_genes_groups(adata, groupby, method="t-test", pts=True, random_state=0)
    sig = adata.uns["rank_genes_groups"]["pts"]
    sig.index.name = "GeneSymbol"
    return sig


def correlation_matrix(adata, identify_by=None):
    if identify_by:
        return pd.DataFrame(
            np.corrcoef(adata.X),
            index=adata.obs.index + "_" + adata.obs[identify_by],
            columns=adata.obs.index + "_" + adata.obs[identify_by],
        )
    return pd.DataFrame(
        np.corrcoef(adata.X), index=adata.obs.index, columns=adata.obs.index
    )


def sort_obs(adata: sc.AnnData, by=None) -> sc.AnnData:
    if by:
        return adata[adata.obs.sort_values(by=by)]
    return adata[adata.obs.sort_index().index]


def fix_index(df: pd.DataFrame, suffix="_"):
    appendents = (
        suffix + df.groupby(level=0).cumcount().astype(str).replace("0", "")
    ).replace(suffix, "")
    return df.set_index(df.index + appendents)


def export_pc_loadings(adata, filepath, n_components=None):
    assert "X_pca" in adata.obs, "Run PC decomposition ('sc.pp.pca') first!"
    if n_components == None:
        n_components = adata.obsm["X_pca"].shape[1]

    with open(filepath, "w") as out:
        out.write(
            "GeneSymbol\t" + "\t".join([f"PC{x+1}" for x in range(n_components)]) + "\n"
        )
        for i in range(adata.shape[1]):
            out.write(
                adata.var.index.values[i]
                + "\t"
                + "\t".join(str(x) for x in adata.varm["PCs"][i, :n_components])
                + "\n"
            )
