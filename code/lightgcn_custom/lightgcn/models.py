from typing import Optional, Union, Tuple
from torch_geometric.typing import Adj, OptTensor

import os
import numpy as np
import torch
from torch import Tensor
from sklearn.metrics import accuracy_score, roc_auc_score
from torch_geometric.nn.models import LightGCN
from torch_geometric.nn import MessagePassing
from torch.nn import Embedding, ModuleList
from torch_geometric.nn.conv import LGConv
from torch_sparse import SparseTensor
from torch import nn
import torch.nn.functional as F
try:
    from transformers.modeling_bert import BertConfig, BertEncoder, BertModel
except:
    from transformers.models.bert.modeling_bert import (
        BertConfig,
        BertEncoder,
        BertModel,
    )


class MyLightGCN(torch.nn.Module):
    """ Initializes MyLightGCN Model
    LightGCN 기본 클래스의 predict_link 등을 그대로 사용한다. 
    https://arxiv.org/abs/2002.02126

    Args:
        num_nodes (int): 그래프의 노드 수 
        embedding_dim (int): 노드 embedding dimension
        num_layers (int): LGConv layer 수 
        embedding_info (list): embedding 할 information dict list 
                       ( name : column name, value : column 의 element 수, weight : 가중치 )
        alpha (float or Tensor, optional): 레이어에 가산될 가중치
        **kwargs (optional): Additional arguments of the underlying
            :class:`~torch_geometric.nn.conv.LGConv` layers.
    """

    def __init__(
            self,
            num_info:dict,
            embedding_dim: int,
            num_layers: int,
            alpha: Optional[Union[float, Tensor]] = None,
            **kwargs,
        ):
            """
            - userid embedding 
            - assessmentItemId embedding
            - assessmentItemId 의 각 속성 값 
                - knowledge tag
                - category value 
            
            - base embedding value 
                - the number of user 
                - the number of item 
            
            - additional information
                - user additional information 
                - item additional information 
            """
            # super().__init__(num_info["n_user"], embedding_dim,num_layers)
            super().__init__()
            self.embedding_dim = embedding_dim
            self.num_layers = num_layers

            if alpha is None:
                alpha = 1. / (num_layers + 1)

            if isinstance(alpha, Tensor):
                assert alpha.size(0) == num_layers + 1
            else:
                alpha = torch.tensor([alpha] * (num_layers + 1))
            self.register_buffer('alpha', alpha)

            self.user_embedding = Embedding(num_info["n_user"], embedding_dim)
            self.item_embedding = Embedding(num_info["n_item"], embedding_dim)
            self.tag_embedding  = Embedding(num_info["n_tags"], embedding_dim)
            self.testId_embedding  = Embedding(num_info["n_testids"], embedding_dim)
            self.bigcat_embedding  = Embedding(num_info["n_bigcat"], embedding_dim)

            """
            self.comb_proj = nn.Sequential(
                # nn.ReLU(),
                nn.Linear(self.args.embedding_dim//3, self.args.embedding_dim),
                # nn.LayerNorm(self.args.hidden_dim)
            )
            """

            self.daydiff_embedding  = Embedding(5, embedding_dim)
            self.convs = ModuleList([LGConv(**kwargs) for _ in range(num_layers)])
        
            # embedding layer 및 convolutional layer 의 weight 초기화 
            self.reset_parameters()

    def reset_parameters(self):
        torch.nn.init.xavier_uniform_(self.user_embedding.weight)
        torch.nn.init.xavier_uniform_(self.item_embedding.weight)
        torch.nn.init.xavier_uniform_(self.tag_embedding.weight)
        torch.nn.init.xavier_uniform_(self.testId_embedding.weight)
        torch.nn.init.xavier_uniform_(self.bigcat_embedding.weight)
        torch.nn.init.xavier_uniform_(self.daydiff_embedding.weight)

        for conv in self.convs:
            conv.reset_parameters()

    def get_embedding(self, edge_index: Adj, additional_info:dict=None, edge_weight: OptTensor = None, 
                      training:bool=True, dropout:float=0.2) -> Tensor:

        item_embedding_weight = self.item_embedding.weight 
        tag_embedding_weight  = self.tag_embedding(additional_info["item"]["KnowledgeTag"] )
        testId_embedding_weight  = self.testId_embedding(additional_info["item"]["testId"] )
        bigcat_embedding_weight  = self.bigcat_embedding(additional_info["item"]["big_category"] )
        daydiff_embedding_weight = self.daydiff_embedding(additional_info["user"]["day_diff"])
        total_embedding_weight = item_embedding_weight

        embedding_list = [
                          tag_embedding_weight,
                          testId_embedding_weight,
                          bigcat_embedding_weight,
                         ]
        
        """ projection 0.821
        embed = torch.cat( [item_embedding_weight,
                    tag_embedding_weight,
                    testId_embedding_weight,
                    bigcat_embedding_weight])
        
        x = self.comb_proj(embed)
        """

        for emb in embedding_list : 
            total_embedding_weight = total_embedding_weight + emb

        total_embedding_weight = total_embedding_weight / (len(embedding_list) + 1 )

        # total_embedding_weight = (  item_embedding_weight + tag_embedding_weight + testId_embedding_weigsht + bigcat_embedding_weight ) / 4

        # if additional_info["item"] is not None :
        #     for k in additional_info["item"] :
        #         self.item_embedding( additional_info["item"][k] )

        x = torch.cat([
            ( self.user_embedding.weight + daydiff_embedding_weight ) / 2, 
              total_embedding_weight
            # total_embedding_weight
            ],dim= 0)
        
        # x = self.embedding.weight
        out = x * self.alpha[0]

        # edge drop out
        edge_index, edge_mask = self.dropout_edge(edge_index,p=dropout,training=training)
        edge_weight = torch.masked_select(edge_weight, edge_mask) 
        self.edge_mask = edge_mask
        
        """
        for i in range(self.num_layers):
            # edge_weight =  torch.ones(edge_index.size(1))
            # print(edge_weight)
            # print(edge_weight.shape)
            x = self.convs[i](x, edge_index, edge_weight = None ) # edge_weight
            out = out + x * self.alpha[i + 1]
        """
            
        return out


    def forward(self, edge_index: Adj, additional_info:dict=None,
                edge_label_index: OptTensor = None, edge_weight: OptTensor = None, 
                training:bool=False, dropout:float=0.2 ) -> Tensor:
        r"""Computes rankings for pairs of nodes.

        Args:
            edge_index (Tensor or SparseTensor): Edge tensor specifying the
                connectivity of the graph.
            additional_info (dict) 
            edge_label_index (Tensor, optional): Edge tensor specifying the
                node pairs for which to compute rankings or probabilities.
                If :obj:`edge_label_index` is set to :obj:`None`, all edges in
                :obj:`edge_index` will be used instead. (default: :obj:`None`)
        """

        if edge_label_index is None:
            if isinstance(edge_index, SparseTensor):
                edge_label_index = torch.stack(edge_index.coo()[:2], dim=0)
            else:
                edge_label_index = edge_index

        out = self.get_embedding(edge_index, additional_info,edge_weight,training=training,dropout=dropout)
        out_src = out[edge_label_index[0]]
        out_dst = out[edge_label_index[1]]

        return (out_src * out_dst).sum(dim=-1)

    def dropout_edge(self,edge_index: Tensor, p: float = 0.5,
                 force_undirected: bool = False,
                 training: bool = True) -> Tuple[Tensor, Tensor]:
        r"""Randomly drops edges from the adjacency matrix
        :obj:`edge_index` with probability :obj:`p` using samples from
        a Bernoulli distribution.

        The method returns (1) the retained :obj:`edge_index`, (2) the edge mask
        or index indicating which edges were retained, depending on the argument
        :obj:`force_undirected`.

        Args:
            edge_index (LongTensor): The edge indices.
            p (float, optional): Dropout probability. (default: :obj:`0.5`)
            force_undirected (bool, optional): If set to :obj:`True`, will either
                drop or keep both edges of an undirected edge.
                (default: :obj:`False`)
            training (bool, optional): If set to :obj:`False`, this operation is a
                no-op. (default: :obj:`True`)

        :rtype: (:class:`LongTensor`, :class:`BoolTensor` or :class:`LongTensor`)

        Examples:

            >>> edge_index = torch.tensor([[0, 1, 1, 2, 2, 3],
            ...                            [1, 0, 2, 1, 3, 2]])
            >>> edge_index, edge_mask = dropout_edge(edge_index)
            >>> edge_index
            tensor([[0, 1, 2, 2],
                    [1, 2, 1, 3]])
            >>> edge_mask # masks indicating which edges are retained
            tensor([ True, False,  True,  True,  True, False])

            >>> edge_index, edge_id = dropout_edge(edge_index,
            ...                                    force_undirected=True)
            >>> edge_index
            tensor([[0, 1, 2, 1, 2, 3],
                    [1, 2, 3, 0, 1, 2]])
            >>> edge_id # indices indicating which edges are retained
            tensor([0, 2, 4, 0, 2, 4])
        """
        if p < 0. or p > 1.:
            raise ValueError(f'Dropout probability has to be between 0 and 1 '
                            f'(got {p}')

        if not training or p == 0.0:
            edge_mask = edge_index.new_ones(edge_index.size(1), dtype=torch.bool)
            return edge_index, edge_mask

        row, col = edge_index

        edge_mask = torch.rand(row.size(0), device=edge_index.device) >= p

        if force_undirected:
            edge_mask[row > col] = False

        edge_index = edge_index[:, edge_mask]

        if force_undirected:
            edge_index = torch.cat([edge_index, edge_index.flip(0)], dim=1)
            edge_mask = edge_mask.nonzero().repeat((2, 1)).squeeze()

        return edge_index, edge_mask

    def predict_link(self, edge_index: Adj, additional_info:dict=None,edge_label_index: OptTensor = None, edge_weight: OptTensor = None,
                     prob: bool = False) -> Tensor:
        r"""Predict links between nodes specified in :obj:`edge_label_index`.

        Args:
            prob (bool): Whether probabilities should be returned. (default:
                :obj:`False`)
        """
        pred = self(edge_index, additional_info,edge_label_index,edge_weight = edge_weight).sigmoid()
        return pred if prob else pred.round()

    def link_pred_loss(self, pred: Tensor, edge_label: Tensor,
                    **kwargs) -> Tensor:
        r"""Computes the model loss for a link prediction objective via the
        :class:`torch.nn.BCEWithLogitsLoss`.

        Args:
            pred (Tensor): The predictions.
            edge_label (Tensor): The ground-truth edge labels.
            **kwargs (optional): Additional arguments of the underlying
                :class:`torch.nn.BCEWithLogitsLoss` loss function.
        """
        loss_fn = torch.nn.BCEWithLogitsLoss(**kwargs)
        return loss_fn(pred, edge_label.to(pred.dtype))

##############################################################################################################
# model test : LightGCN + Last Query Attention
##############################################################################################################

class Feed_Forward_block(nn.Module):
    """
    out =  Relu( M_out*w1 + b1) *w2 + b2
    """
    def __init__(self, dim_ff):
        super().__init__()
        self.layer1 = nn.Linear(in_features=dim_ff, out_features=dim_ff)
        self.layer2 = nn.Linear(in_features=dim_ff, out_features=dim_ff)

    def forward(self,ffn_in):
        return self.layer2(F.relu(self.layer1(ffn_in)))

class MyLightGCNWithAttn(MyLightGCN):
    """ Initializes MyLightGCN Model
    LightGCN 기본 클래스의 predict_link 등을 그대로 사용한다. 
    https://arxiv.org/abs/2002.02126

    Args:
        num_nodes (int): 그래프의 노드 수 
        embedding_dim (int): 노드 embedding dimension
        num_layers (int): LGConv layer 수 
        embedding_info (list): embedding 할 information dict list 
                       ( name : column name, value : column 의 element 수, weight : 가중치 )
        alpha (float or Tensor, optional): 레이어에 가산될 가중치
        **kwargs (optional): Additional arguments of the underlying
            :class:`~torch_geometric.nn.conv.LGConv` layers.
    """

    def __init__(
            self,
            num_info:dict,
            embedding_dim: int,
            num_layers: int,
            alpha: Optional[Union[float, Tensor]] = None,
            **kwargs,
        ):
            """
            - userid embedding 
            - assessmentItemId embedding
            - assessmentItemId 의 각 속성 값 
                - knowledge tag
                - category value 
            
            - base embedding value 
                - the number of user 
                - the number of item 
            
            - additional information
                - user additional information 
                - item additional information 
            """
            # super().__init__(num_info["n_user"], embedding_dim,num_layers)
            super().__init__(num_info, embedding_dim, num_layers, alpha )
            use_cuda = torch.cuda.is_available()
            self.device = torch.device("cuda" if use_cuda else "cpu")
            """ for attention 
            """
            # 기존 keetar님 솔루션에서는 Positional Embedding은 사용되지 않습니다
            # 하지만 사용 여부는 자유롭게 결정해주세요 :)
            # self.embedding_position = nn.Embedding(self.args.max_seq_len, self.hidden_dim)
            
            # Encoder
            self.query = nn.Linear(in_features=self.embedding_dim, out_features=self.embedding_dim)
            self.key = nn.Linear(in_features=self.embedding_dim, out_features=self.embedding_dim)
            self.value = nn.Linear(in_features=self.embedding_dim, out_features=self.embedding_dim)

            self.attn = nn.MultiheadAttention(embed_dim=self.embedding_dim, num_heads=4)
            self.mask = None # last query에서는 필요가 없지만 수정을 고려하여서 넣어둠
            self.ffn = Feed_Forward_block(self.embedding_dim)      

            self.ln1 = nn.LayerNorm(self.embedding_dim)
            self.ln2 = nn.LayerNorm(self.embedding_dim)

            # LSTM
            self.lstm = nn.LSTM(
                self.embedding_dim, self.embedding_dim, self.num_layers, batch_first=True)

            # GRU
            self.gru = nn.GRU(
                self.embedding_dim, self.embedding_dim, self.num_layers, batch_first=True)

            # Fully connected layer
            self.fc = nn.Linear(self.embedding_dim, 1)
        
            self.activation = nn.Sigmoid()
        
    def init_hidden(self, batch_size):
        h = torch.zeros(
            self.num_layers,
            self.embedding_dim)
        h = h.to(self.device)

        c = torch.zeros(
            self.num_layers,
            self.embedding_dim)
        c = c.to(self.device)

        return (h, c)

    def get_embedding(self, edge_index: Adj, additional_info:dict=None, edge_weight: OptTensor = None, 
                      training:bool=True, dropout:float=0.2) -> Tensor:

        item_embedding_weight = self.item_embedding.weight 
        tag_embedding_weight  = self.tag_embedding(additional_info["item"]["KnowledgeTag"] )
        testId_embedding_weight  = self.testId_embedding(additional_info["item"]["testId"] )
        bigcat_embedding_weight  = self.bigcat_embedding(additional_info["item"]["big_category"] )
        daydiff_embedding_weight = self.daydiff_embedding(additional_info["user"]["day_diff"])
        total_embedding_weight = item_embedding_weight

        embedding_list = [
                          tag_embedding_weight,
                          testId_embedding_weight,
                          bigcat_embedding_weight,
                         ]
        
        """ projection 0.821
        embed = torch.cat( [item_embedding_weight,
                    tag_embedding_weight,
                    testId_embedding_weight,
                    bigcat_embedding_weight])
        
        x = self.comb_proj(embed)
        """

        for emb in embedding_list : 
            total_embedding_weight = total_embedding_weight + emb

        total_embedding_weight = total_embedding_weight / (len(embedding_list) + 1 )

        # total_embedding_weight = (  item_embedding_weight + tag_embedding_weight + testId_embedding_weigsht + bigcat_embedding_weight ) / 4

        # if additional_info["item"] is not None :
        #     for k in additional_info["item"] :
        #         self.item_embedding( additional_info["item"][k] )

        x = torch.cat([
            ( self.user_embedding.weight + daydiff_embedding_weight ) / 2, 
              total_embedding_weight
            # total_embedding_weight
            ],dim= 0)
        
                      
        q = self.query(x)
        k = self.key(x)
        v = self.value(x)
        out, _ = self.attn(q, k, v)
        out = self.ln1(out)
        out = self.ffn(out)
        out = self.ln2(out)
        
        # x = self.embedding.weight
        out = out * self.alpha[0]

        # edge drop out
        edge_index, edge_mask = self.dropout_edge(edge_index,p=dropout,training=training)
        edge_weight = torch.masked_select(edge_weight, edge_mask) 
        self.edge_mask = edge_mask
        
        for i in range(self.num_layers):
            # edge_weight =  torch.ones(edge_index.size(1))
            # print(edge_weight)
            # print(edge_weight.shape)
            x = self.convs[i](x, edge_index, edge_weight = edge_weight ) # edge_weight
            out = out + x * self.alpha[i + 1]
        return out


##############################################################################################################
# model test : LightGCN + Last Query Attention
##############################################################################################################


def build( num_info:dict, weight=None, logger=None, **kwargs):
    model = MyLightGCN( num_info=num_info, **kwargs)
    if weight:
        if not os.path.isfile(weight):
            logger.fatal("Model Weight File Not Exist")
        logger.info("Load model")
        state = torch.load(weight)["model"]
        model.load_state_dict(state)
        return model
    else:
        logger.info("No load model")
        return model


def train(
    model,
    train_data,
    additional_data = None,
    valid_data=None,
    n_epoch=100,
    early_stop = 10,
    learning_rate=0.01,
    use_wandb=False,
    weight=None,
    logger=None,
):
    model.train()

    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    if not os.path.exists(weight):
        os.makedirs(weight)

    if valid_data is None:
        eids = np.arange(len(train_data["label"]))
        eids = np.random.permutation(eids)[:1000]
        edge, label = train_data["edge"], train_data["label"]
        weight = train_data["weight"]
        label = label.to("cpu").detach().numpy()
        weight = weight.to("cpu").detach().numpy()
        valid_data = dict(edge=edge[:, eids], label=label[eids], weight=weight[eids])

    logger.info(f"Training Started : n_epoch={n_epoch}")
    best_auc, best_epoch = 0, -1
    stop_check = 0 
    
    for e in range(n_epoch):
        # forward
        pred = model(train_data["edge"],additional_data,edge_weight = train_data["weight"])
        loss = model.link_pred_loss(pred, train_data["label"])

        # backward
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        with torch.no_grad():
            # additional_data 는 user 와 item 을 key 로 가지고, 각 key 의 값도 dictionary 
            # { user: {} , item : { knowledgeTag : tensor, ...} }
            prob = model.predict_link(valid_data["edge"],additional_data,edge_weight = valid_data["weight"],prob=True)
            prob = prob.detach().cpu().numpy()
            acc = accuracy_score(valid_data["label"].cpu().numpy(), prob > 0.5)
            auc = roc_auc_score(valid_data["label"].cpu().numpy(), prob)
            logger.info(
                f" * In epoch {(e+1):04}, loss={loss:.03f}, acc={acc:.03f}, AUC={auc:.03f}"
            )
            if use_wandb:
                import wandb

                wandb.log(dict(loss=loss, acc=acc, auc=auc))

        if weight:
            if auc > best_auc:
                logger.info(
                    f" * In epoch {(e+1):04}, loss={loss:.03f}, acc={acc:.03f}, AUC={auc:.03f}, Best AUC"
                )
                best_auc, best_epoch = auc, e
                torch.save(
                    {"model": model.state_dict(), "epoch": e + 1},
                    os.path.join(weight, f"best_model.pt"),
                )
                stop_check = 0 
            elif auc < best_auc : 
                stop_check += 1 
            
            if ( stop_check >= early_stop ):
                break

            
    torch.save(
        {"model": model.state_dict(), "epoch": e + 1},
        os.path.join(weight, f"last_model.pt"),
    )
    logger.info(f"Best Weight Confirmed : {best_epoch+1}'th epoch")


def train_kfold(
    model,
    train_data,
    additional_data = None,
    valid_data=None,
    n_epoch=100,
    early_stop = 10,
    learning_rate=0.01,
    dropout=0.2,
    use_wandb=False,
    weight=None,
    logger=None,
):
    model.train()

    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

    if not os.path.exists(weight):
        os.makedirs(weight)

    if valid_data is None:
        eids = np.arange(len(train_data["label"]))
        eids = np.random.permutation(eids)[:1000]
        edge, label = train_data["edge"], train_data["label"]
        weight = train_data["weight"]
        label = label.to("cpu").detach().numpy()
        weight = weight.to("cpu").detach().numpy()
        valid_data = [dict(edge=edge[:, eids], label=label[eids], weight=weight[eids])]

    logger.info(f"Training Started : n_epoch={n_epoch}")
    best_auc, best_epoch = 0, -1
    stop_check = 0 
    
    for k_idx ,( cur_train_data, cur_valid_data) in enumerate(zip(train_data,valid_data)) :
        # forward
        best_auc, best_epoch = 0, -1
        stop_check = 0 
        
        for e in range(n_epoch):
            pred = model(cur_train_data["edge"],additional_data,edge_weight = cur_train_data["weight"],training=True, dropout=dropout)
            loss = model.link_pred_loss(pred, cur_train_data["label"])

            # backward
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            with torch.no_grad():
                # additional_data 는 user 와 item 을 key 로 가지고, 각 key 의 값도 dictionary 
                # { user: {} , item : { knowledgeTag : tensor, ...} }
                prob = model.predict_link(cur_valid_data["edge"],additional_data,edge_weight = cur_valid_data["weight"],prob=True)
                prob = prob.detach().cpu().numpy()
                acc = accuracy_score(cur_valid_data["label"].cpu().numpy(), prob > 0.5)
                auc = roc_auc_score(cur_valid_data["label"].cpu().numpy(), prob)
                logger.info(
                    f" * In epoch {(e+1):04}, loss={loss:.03f}, acc={acc:.03f}, AUC={auc:.03f}"
                )
                if use_wandb:
                    import wandb

                    wandb.log(dict(loss=loss, acc=acc, auc=auc))

            if weight:
                if auc > best_auc:
                    logger.info(
                        f" * In epoch {(e+1):04}, loss={loss:.03f}, acc={acc:.03f}, AUC={auc:.03f}, Best AUC"
                    )
                    best_auc, best_epoch = auc, e
                    torch.save(
                        {"model": model.state_dict(), "epoch": e + 1},
                        os.path.join(weight, f"best_model_{k_idx}.pt"),
                    )
                    stop_check = 0 
                elif auc < best_auc : 
                    stop_check += 1 
                
                if ( stop_check >= early_stop ):
                    break

                
        torch.save(
            {"model": model.state_dict(), "epoch": e + 1},
            os.path.join(weight, f"last_model.pt"),
        )
        logger.info(f"Best Weight Confirmed : {best_epoch+1}'th epoch")

def inference(model, data,additional_data, logger=None):
    model.eval()
    with torch.no_grad():
        pred = model.predict_link(data["edge"],additional_data,edge_weight=data["weight"], prob=True)
        return pred
