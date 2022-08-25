import backend as F
import dgl.nn
import dgl
import numpy as np
import pytest
import torch as th
from dgl import DGLError
from dgl.base import DGLWarning
from dgl.geometry import neighbor_matching, farthest_point_sampler
from test_utils import parametrize_idtype
from test_utils.graph_cases import get_cases


def test_fps():
    N = 1000
    batch_size = 5
    sample_points = 10
    x = th.tensor(np.random.uniform(size=(batch_size, int(N/batch_size), 3)))
    ctx = F.ctx()
    if F.gpu_ctx():
        x = x.to(ctx)
    res = farthest_point_sampler(x, sample_points)
    assert res.shape[0] == batch_size
    assert res.shape[1] == sample_points
    assert res.sum() > 0


def test_fps_start_idx():
    N = 1000
    batch_size = 5
    sample_points = 10
    x = th.tensor(np.random.uniform(size=(batch_size, int(N/batch_size), 3)))
    ctx = F.ctx()
    if F.gpu_ctx():
        x = x.to(ctx)
    res = farthest_point_sampler(x, sample_points, start_idx=0)
    assert th.any(res[:, 0] == 0)


@pytest.mark.parametrize('algorithm', ['bruteforce-blas', 'bruteforce', 'kd-tree'])
@pytest.mark.parametrize('dist', ['euclidean', 'cosine'])
def test_knn_cpu(algorithm, dist):
    x = th.randn(8, 3).to(F.cpu())
    kg = dgl.nn.KNNGraph(3)
    if dist == 'euclidean':
        d = th.cdist(x, x).to(F.cpu())
    else:
        x = x + th.randn(1).item()
        tmp_x = x / (1e-5 + F.sqrt(F.sum(x * x, dim=1, keepdims=True)))
        d = 1 - F.matmul(tmp_x, tmp_x.T).to(F.cpu())

    def check_knn(g, x, start, end, k):
        assert g.device == x.device
        for v in range(start, end):
            src, _ = g.in_edges(v)
            src = set(src.numpy())
            i = v - start
            src_ans = set(th.topk(d[start:end, start:end][i], k, largest=False)[1].numpy() + start)
            assert src == src_ans

    # check knn with 2d input
    g = kg(x, algorithm, dist)
    check_knn(g, x, 0, 8, 3)

    # check knn with 3d input
    g = kg(x.view(2, 4, 3), algorithm, dist)
    check_knn(g, x, 0, 4, 3)
    check_knn(g, x, 4, 8, 3)

    # check segmented knn
    kg = dgl.nn.SegmentedKNNGraph(3)
    g = kg(x, [3, 5], algorithm, dist)
    check_knn(g, x, 0, 3, 3)
    check_knn(g, x, 3, 8, 3)

    # check k > num_points
    kg = dgl.nn.KNNGraph(10)
    with pytest.warns(DGLWarning):
        g = kg(x, algorithm, dist)
    check_knn(g, x, 0, 8, 8)

    with pytest.warns(DGLWarning):
        g = kg(x.view(2, 4, 3), algorithm, dist)
    check_knn(g, x, 0, 4, 4)
    check_knn(g, x, 4, 8, 4)

    kg = dgl.nn.SegmentedKNNGraph(5)
    with pytest.warns(DGLWarning):
        g = kg(x, [3, 5], algorithm, dist)
    check_knn(g, x, 0, 3, 3)
    check_knn(g, x, 3, 8, 3)

    # check k == 0
    kg = dgl.nn.KNNGraph(0)
    with pytest.raises(DGLError):
        g = kg(x, algorithm, dist)
    kg = dgl.nn.SegmentedKNNGraph(0)
    with pytest.raises(DGLError):
        g = kg(x, [3, 5], algorithm, dist)

    # check empty
    x_empty = th.tensor([])
    kg = dgl.nn.KNNGraph(3)
    with pytest.raises(DGLError):
        g = kg(x_empty, algorithm, dist)
    kg = dgl.nn.SegmentedKNNGraph(3)
    with pytest.raises(DGLError):
        g = kg(x_empty, [3, 5], algorithm, dist)

@pytest.mark.parametrize('algorithm', ['bruteforce-blas', 'bruteforce', 'bruteforce-sharemem'])
@pytest.mark.parametrize('dist', ['euclidean', 'cosine'])
@pytest.mark.parametrize('exclude_self', [False, True])
@pytest.mark.parametrize('output_batch', [False, True])
def test_knn_cuda(algorithm, dist, exclude_self, output_batch):
    if not th.cuda.is_available():
        return
    x = th.randn(8, 3).to(F.cuda())
    kg = dgl.nn.KNNGraph(3)
    if dist == 'euclidean':
        d = th.cdist(x, x).to(F.cpu())
    else:
        x = x + th.randn(1).item()
        tmp_x = x / (1e-5 + F.sqrt(F.sum(x * x, dim=1, keepdims=True)))
        d = 1 - F.matmul(tmp_x, tmp_x.T).to(F.cpu())

    def check_knn(g, x, start, end, k, exclude_self, check_indices=True):
        assert g.device == x.device
        g = g.to(F.cpu())
        for v in range(start, end):
            src, _ = g.in_edges(v)
            src = set(src.numpy())
            assert len(src) == k
            if check_indices:
                i = v - start
                src_ans = set(th.topk(d[start:end, start:end][i], k + (1 if exclude_self else 0), largest=False)[1].numpy() + start)
                if exclude_self:
                    # remove self
                    src_ans.remove(v)
                assert src == src_ans

    def check_batch(g, k, output_batch, expected_batch_info):
        if output_batch:
            assert F.array_equal(g.batch_num_nodes(), F.tensor(expected_batch_info))
            assert F.array_equal(g.batch_num_edges(), k*F.tensor(expected_batch_info))
        else:
            assert F.array_equal(g.batch_num_nodes(), F.sum(F.tensor(expected_batch_info), 0, keepdims=True))
            assert F.array_equal(g.batch_num_edges(), k*F.sum(F.tensor(expected_batch_info), 0, keepdims=True))
        return

    # check knn with 2d input
    g = kg(x, algorithm, dist, exclude_self, output_batch)
    check_knn(g, x, 0, 8, 3, exclude_self)
    check_batch(g, 3, output_batch, [8])

    # check knn with 3d input
    g = kg(x.view(2, 4, 3), algorithm, dist, exclude_self, output_batch)
    check_knn(g, x, 0, 4, 3, exclude_self)
    check_knn(g, x, 4, 8, 3, exclude_self)
    check_batch(g, 3, output_batch, [4, 4])

    # check segmented knn
    # there are only 2 edges per node possible when exclude_self with 3 nodes in the segment
    # and this test case isn't supposed to warn, so limit it when exclude_self is True
    adjusted_k = 3 - (1 if exclude_self else 0)
    kg = dgl.nn.SegmentedKNNGraph(adjusted_k)
    g = kg(x, [3, 5], algorithm, dist, exclude_self, output_batch)
    check_knn(g, x, 0, 3, adjusted_k, exclude_self)
    check_knn(g, x, 3, 8, adjusted_k, exclude_self)
    check_batch(g, adjusted_k, output_batch, [3, 5])

    # check k > num_points
    kg = dgl.nn.KNNGraph(10)
    with pytest.warns(DGLWarning):
        g = kg(x, algorithm, dist, exclude_self, output_batch)
    # there are only 7 edges per node possible when exclude_self with 8 nodes total
    adjusted_k = 8 - (1 if exclude_self else 0)
    check_knn(g, x, 0, 8, adjusted_k, exclude_self)
    check_batch(g, adjusted_k, output_batch, [8])

    with pytest.warns(DGLWarning):
        g = kg(x.view(2, 4, 3), algorithm, dist, exclude_self, output_batch)
    # there are only 3 edges per node possible when exclude_self with 4 nodes per segment
    adjusted_k = 4 - (1 if exclude_self else 0)
    check_knn(g, x, 0, 4, adjusted_k, exclude_self)
    check_knn(g, x, 4, 8, adjusted_k, exclude_self)
    check_batch(g, adjusted_k, output_batch, [4, 4])

    kg = dgl.nn.SegmentedKNNGraph(5)
    with pytest.warns(DGLWarning):
        g = kg(x, [3, 5], algorithm, dist, exclude_self, output_batch)
    # there are only 2 edges per node possible when exclude_self in the segment with
    # only 3 nodes, and the current implementation reduces k for all segments
    # in that case
    adjusted_k = 3 - (1 if exclude_self else 0)
    check_knn(g, x, 0, 3, adjusted_k, exclude_self)
    check_knn(g, x, 3, 8, adjusted_k, exclude_self)
    check_batch(g, adjusted_k, output_batch, [3, 5])

    # check k == 0
    # that's valid for exclude_self, but -1 is not, so check -1 instead for exclude_self
    adjusted_k = 0 - (1 if exclude_self else 0)
    kg = dgl.nn.KNNGraph(adjusted_k)
    with pytest.raises(DGLError):
        g = kg(x, algorithm, dist, exclude_self, output_batch)
    kg = dgl.nn.SegmentedKNNGraph(adjusted_k)
    with pytest.raises(DGLError):
        g = kg(x, [3, 5], algorithm, dist, exclude_self, output_batch)

    # check empty
    x_empty = th.tensor([])
    kg = dgl.nn.KNNGraph(3)
    with pytest.raises(DGLError):
        g = kg(x_empty, algorithm, dist, exclude_self, output_batch)
    kg = dgl.nn.SegmentedKNNGraph(3)
    with pytest.raises(DGLError):
        g = kg(x_empty, [3, 5], algorithm, dist, exclude_self, output_batch)

    # check all coincident points
    x = th.zeros((20, 3)).to(F.cuda())
    kg = dgl.nn.KNNGraph(3)
    g = kg(x, algorithm, dist, exclude_self, output_batch)
    # different algorithms may break the tie differently, so don't check the indices
    check_knn(g, x, 0, 20, 3, exclude_self, False)
    check_batch(g, 3, output_batch, [20])

    # check all coincident points
    kg = dgl.nn.SegmentedKNNGraph(3)
    g = kg(x, [4, 7, 5, 4], algorithm, dist, exclude_self, output_batch)
    # different algorithms may break the tie differently, so don't check the indices
    check_knn(g, x,  0,  4, 3, exclude_self, False)
    check_knn(g, x,  4, 11, 3, exclude_self, False)
    check_knn(g, x, 11, 16, 3, exclude_self, False)
    check_knn(g, x, 16, 20, 3, exclude_self, False)
    check_batch(g, 3, output_batch, [4, 7, 5, 4])


@parametrize_idtype
@pytest.mark.parametrize('g', get_cases(['homo'], exclude=['dglgraph']))
@pytest.mark.parametrize('weight', [True, False])
@pytest.mark.parametrize('relabel', [True, False])
def test_edge_coarsening(idtype, g, weight, relabel):
    num_nodes = g.num_nodes()
    g = dgl.to_bidirected(g)
    g = g.astype(idtype).to(F.ctx())
    edge_weight = None
    if weight:
        edge_weight = F.abs(F.randn((g.num_edges(),))).to(F.ctx())
    node_labels = neighbor_matching(g, edge_weight, relabel_idx=relabel)
    unique_ids, counts = th.unique(node_labels, return_counts=True)
    num_result_ids = unique_ids.size(0)

    # shape correct
    assert node_labels.shape == (g.num_nodes(),)

    # all nodes marked
    assert F.reduce_sum(node_labels < 0).item() == 0

    # number of unique node ids correct.
    assert num_result_ids >= num_nodes // 2 and num_result_ids <= num_nodes

    # each unique id has <= 2 nodes
    assert F.reduce_sum(counts > 2).item() == 0

    # if two nodes have the same id, they must be neighbors
    idxs = F.arange(0, num_nodes, idtype)
    for l in unique_ids:
        l = l.item()
        idx = idxs[(node_labels == l)]
        if idx.size(0) == 2:
            u, v = idx[0].item(), idx[1].item()
            assert g.has_edges_between(u, v)


if __name__ == '__main__':
    test_fps()
    test_fps_start_idx()
    test_knn()
