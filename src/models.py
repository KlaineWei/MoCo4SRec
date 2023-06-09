# -*- coding: utf-8 -*-
import math
import os
import pickle
from tqdm import tqdm
import random
import copy

import torch
import torch.nn as nn
import gensim

from modules import Encoder, LayerNorm


class SASRecModel(nn.Module):
    def __init__(self, args):
        super(SASRecModel, self).__init__()
        self.item_embeddings = nn.Embedding(args.item_size, args.hidden_size, padding_idx=0)
        self.position_embeddings = nn.Embedding(args.max_seq_length, args.hidden_size)
        self.item_encoder = Encoder(args)
        # self.moco_encoder = MoCo(args)
        self.LayerNorm = LayerNorm(args.hidden_size, eps=1e-12)
        self.dropout = nn.Dropout(args.hidden_dropout_prob)
        self.args = args

        self.criterion = nn.BCELoss(reduction='none')
        self.apply(self.init_weights)

        # projection head for contrastive learn task
        self.input_dim = self.args.max_seq_length * self.args.hidden_size
        self.ph_dim = self.args.dim
        self.projection = nn.Sequential(nn.Linear(self.input_dim, self.ph_dim, bias=False),
                                        nn.BatchNorm1d(self.ph_dim, eps=1e-12, affine=True),
                                        nn.ReLU(inplace=True),
                                        nn.Linear(self.ph_dim, self.ph_dim, bias=False),
                                        nn.BatchNorm1d(self.ph_dim, eps=1e-12, affine=True))

        # moco params
        self.dim = args.dim
        self.K = args.k
        self.m = args.m
        self.T = args.t
        self.phi = args.phi

        self.encoder_k = Encoder(args)

        for param, param_k in zip(self.item_encoder.parameters(), self.encoder_k.parameters()):
            param_k.data.copy_(param.data)  # initialize
            param_k.requires_grad = False  # not update by gradient

        # create the queue
        self.register_buffer("queue", torch.randn(self.dim, self.K))
        self.queue = nn.functional.normalize(self.queue, dim=0)

        self.register_buffer("queue_ptr", torch.zeros(1, dtype=torch.long))

    # Positional Embedding
    def add_position_embedding(self, sequence, shuffle=False, usenoise=False):

        seq_length = sequence.size(1)
        position_ids = torch.arange(seq_length, dtype=torch.long, device=sequence.device)
        position_ids = position_ids.unsqueeze(0).expand_as(sequence)
        # add token shuffle
        if shuffle:
            position_ids = position_ids[:, torch.randperm(position_ids.size()[1])]

        item_embeddings = self.item_embeddings(sequence)
        position_embeddings = self.position_embeddings(position_ids)

        x = 0.01 + torch.zeros(position_embeddings.shape[0], position_embeddings.shape[1], position_embeddings.shape[2],
                               dtype=torch.float32, device=sequence.device)  # add guassian noise
        noise = torch.normal(mean=torch.tensor([0.0]).to(sequence.device), std=x).to(
            sequence.device)  # add guassian noise.

        sequence_emb = item_embeddings + position_embeddings
        if usenoise:
            sequence_emb += noise
        sequence_emb = self.LayerNorm(sequence_emb)
        sequence_emb = self.dropout(sequence_emb)

        return sequence_emb
    
    # Cutoff Embedding in row, coloum or random direction
    def cutoff_embeddings(self, sequence_emb, attention_mask, direction, rate):
        bsz, seq_len, emb_size = sequence_emb.shape
        cutoff_embeddings = []
        for bsz_id in range(bsz):
            sample_embedding = sequence_emb[bsz_id]
            sample_mask = attention_mask[bsz_id]
            if direction == "row":
                num_dimensions = sample_mask.sum().int().item()  # number of tokens
                dim_index = 0
            elif direction == "column":
                num_dimensions = emb_size  # number of features
                dim_index = 1
            elif direction == "random":
                num_dimensions = sample_mask.sum().int().item() * emb_size
                dim_index = 0
            else:
                raise ValueError(f"direction should be either row or column, but got {direction}")
            num_cutoff_indexes = int(num_dimensions * rate)
            if num_cutoff_indexes < 0 or num_cutoff_indexes > num_dimensions:
                raise ValueError(f"number of cutoff dimensions should be in (0, {num_dimensions}), but got {num_cutoff_indexes}")
            indexes = list(range(num_dimensions))
            import random
            random.shuffle(indexes)
            cutoff_indexes = indexes[:num_cutoff_indexes]
            if direction == "random":
                sample_embedding = sample_embedding.reshape(-1)
            cutoff_embedding = torch.index_fill(sample_embedding, dim_index, torch.tensor(cutoff_indexes, dtype=torch.long).to(device=sequence_emb.device), 0.0)
            if direction == "random":
                cutoff_embedding = cutoff_embedding.reshape(seq_len, emb_size)
            cutoff_embeddings.append(cutoff_embedding.unsqueeze(0))
        cutoff_embeddings = torch.cat(cutoff_embeddings, 0)
        assert cutoff_embeddings.shape == sequence_emb.shape, (cutoff_embeddings.shape, sequence_emb.shape)
        return cutoff_embeddings

    # model same as SASRec
    def transformer_encoder(self, input_ids, cutoff=False, shuffle=False, noise=False):

        attention_mask = (input_ids > 0).long()
        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)  # torch.int64
        max_len = attention_mask.size(-1)
        attn_shape = (1, max_len, max_len)
        subsequent_mask = torch.triu(torch.ones(attn_shape), diagonal=1)  # torch.uint8
        subsequent_mask = (subsequent_mask == 0).unsqueeze(1)
        subsequent_mask = subsequent_mask.long()

        if self.args.cuda_condition:
            subsequent_mask = subsequent_mask.cuda()

        extended_attention_mask = extended_attention_mask * subsequent_mask
        extended_attention_mask = extended_attention_mask.to(dtype=next(self.parameters()).dtype)  # fp16 compatibility
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0

        sequence_emb = self.add_position_embedding(input_ids, shuffle=shuffle, usenoise=noise)
        
        if cutoff:
            sequence_emb = self.cutoff_embeddings(sequence_emb, attention_mask, self.args.direction, self.args.cutoff_rate)

        item_encoded_layers = self.item_encoder(sequence_emb,
                                                extended_attention_mask,
                                                output_all_encoded_layers=True)

        sequence_output = item_encoded_layers[-1]
        return sequence_output

    @torch.no_grad()
    def _momentum_update_key_encoder(self):
        """
        Momentum update of the key encoder
        """
        for param_q, param_k in zip(self.item_encoder.parameters(), self.encoder_k.parameters()):
            param_k.data = param_k.data * self.m + param_q.data * (1. - self.m)

    @torch.no_grad()
    def _dequeue_and_enqueue(self, keys):
        # gather keys before updating queue
        # keys = concat_all_gather(keys)

        batch_size = keys.shape[0]
        # print("batch size:{}", batch_size)
        # print("queue shape:{}", self.queue.shape)

        ptr = int(self.queue_ptr)
        # print("ptr:{}", ptr)
        # assert self.K % batch_size == 0  # for simplicity

        # replace the keys at ptr (dequeue and enqueue)
        if ptr + batch_size >= self.K:
            self.queue[:, ptr:self.K] = keys[:(self.K - ptr)].T
            self.queue[:, :(ptr + batch_size - self.K)] = keys[(self.K - ptr):].T
        else:
            self.queue[:, ptr:(ptr + batch_size)] = keys.T
        # out_ids = torch.arange(batch_size).cuda()
        # out_ids = torch.fmod(out_ids + ptr, self.K).long()
        # self.queue.index_copy_(1, out_ids, keys)

        ptr = (ptr + batch_size) % self.K  # move pointer
        # print("ptr:{}", ptr)

        self.queue_ptr[0] = ptr

    ######################
    ### from MoCo repo ###
    ######################
    def batch_shuffle_single_gpu(self, x):
        """
        Batch shuffle, for making use of BatchNorm.
        """
        # random shuffle index
        idx_shuffle = torch.randperm(x.shape[0]).cuda()

        # index for restoring
        idx_unshuffle = torch.argsort(idx_shuffle)

        return x[idx_shuffle], idx_unshuffle

    ######################
    ### from MoCo repo ###
    ######################
    def batch_unshuffle_single_gpu(self, x, idx_unshuffle):
        """
        Undo batch shuffle.
        """
        return x[idx_unshuffle]

    # moco
    def moco_trans_encoder(self, input_ids, cutoff=False, shuffle=False, noise=False):

        attention_mask = (input_ids > 0).long()
        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)  # torch.int64
        max_len = attention_mask.size(-1)
        attn_shape = (1, max_len, max_len)
        subsequent_mask = torch.triu(torch.ones(attn_shape), diagonal=1)  # torch.uint8
        subsequent_mask = (subsequent_mask == 0).unsqueeze(1)
        subsequent_mask = subsequent_mask.long()

        if self.args.cuda_condition:
            subsequent_mask = subsequent_mask.cuda()

        extended_attention_mask = extended_attention_mask * subsequent_mask
        extended_attention_mask = extended_attention_mask.to(dtype=next(self.parameters()).dtype)  # fp16 compatibility
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0

        sequence_emb = self.add_position_embedding(input_ids,  shuffle=shuffle, noise=noise)
        
        if cutoff:
            sequence_emb = self.cutoff_embeddings(sequence_emb, attention_mask, self.args.direction, self.args.cutoff_rate)

        size = len(sequence_emb) // 2
        sequence_emb = sequence_emb.cuda()

        # q
        q = self.item_encoder(sequence_emb[:size], extended_attention_mask[:size],
                              output_all_encoded_layers=True)  # queries: NxC
        q = q[-1]
        q = q.view(sequence_emb[:size].shape[0], -1)
        q = nn.functional.normalize(q, dim=1)
        if self.args.projection_head:
            q = self.projection(q)

        # k
        # compute key features
        with torch.no_grad():  # no gradient to keys
            self._momentum_update_key_encoder()  # update the key encoder

            # shuffle for making use of BN
            # im_k, idx_unshuffle = self._batch_shuffle_ddp(im_k)

            k = self.encoder_k(sequence_emb[size:], extended_attention_mask[size:],
                               output_all_encoded_layers=True)  # keys: NxC
            k = k[-1]
            k = k.view(sequence_emb[size:].shape[0], -1)
            k = nn.functional.normalize(k, dim=1)
            if self.args.projection_head:
                k = self.projection(k)

        # compute logits
        # Einstein sum is more intuitive
        # positive logits: Nx1
        l_pos = torch.einsum('nc,nc->n', [q, k]).unsqueeze(-1)
        # print("q's shape:{}, k's shape(): {}, l_pos's shape(): {}", q.shape, k.shape, l_pos.shape)
        # negative logits: NxK
        l_neg = torch.einsum('nc,ck->nk', [q, self.queue.clone().detach()])
        # print("queue's shape: {}, l_neg's shape: {}", self.queue.shape, l_neg.shape)

        weights = torch.where(l_neg > self.phi, 0, 1)

        l_neg = l_neg * weights

        # logits: Nx(1+K)
        logits = torch.cat([l_pos, l_neg], dim=1)

        # apply temperature
        logits /= self.T

        # labels: positive key indicators
        labels = torch.zeros(logits.shape[0], dtype=torch.long).cuda()

        # dequeue and enqueue
        self._dequeue_and_enqueue(k)

        return logits, labels

    def init_weights(self, module):
        """ Initialize the weights.
        """
        if isinstance(module, (nn.Linear, nn.Embedding)):
            # Slightly different from the TF version which uses truncated_normal for initialization
            # cf https://github.com/pytorch/pytorch/pull/5617
            module.weight.data.normal_(mean=0.0, std=self.args.initializer_range)
        elif isinstance(module, LayerNorm):
            module.bias.data.zero_()
            module.weight.data.fill_(1.0)
        if isinstance(module, nn.Linear) and module.bias is not None:
            module.bias.data.zero_()


class OnlineItemSimilarity:

    def __init__(self, item_size):
        self.item_size = item_size
        self.item_embeddings = None
        self.cuda_condition = torch.cuda.is_available()
        self.device = torch.device("cuda" if self.cuda_condition else "cpu")
        self.total_item_list = torch.tensor([i for i in range(self.item_size)],
                                            dtype=torch.long).to(self.device)
        self.max_score, self.min_score = self.get_maximum_minimum_sim_scores()

    def update_embedding_matrix(self, item_embeddings):
        self.item_embeddings = copy.deepcopy(item_embeddings)
        self.base_embedding_matrix = self.item_embeddings(self.total_item_list)

    def get_maximum_minimum_sim_scores(self):
        max_score, min_score = -1, 100
        for item_idx in range(1, self.item_size):
            try:
                item_vector = self.item_embeddings(item_idx).view(-1, 1)
                item_similarity = torch.mm(self.base_embedding_matrix, item_vector).view(-1)
                max_score = max(torch.max(item_similarity), max_score)
                min_score = min(torch.min(item_similarity), min_score)
            except:
                continue
        return max_score, min_score

    def most_similar(self, item_idx, top_k=1, with_score=False):
        item_idx = torch.tensor(item_idx, dtype=torch.long).to(self.device)
        item_vector = self.item_embeddings(item_idx).view(-1, 1)
        item_similarity = torch.mm(self.base_embedding_matrix, item_vector).view(-1)
        item_similarity = (self.max_score - item_similarity) / (self.max_score - self.min_score)
        # remove item idx itself
        values, indices = item_similarity.topk(top_k + 1)
        if with_score:
            item_list = indices.tolist()
            score_list = values.tolist()
            if item_idx in item_list:
                idd = item_list.index(item_idx)
                item_list.remove(item_idx)
                score_list.pop(idd)
            return list(zip(item_list, score_list))
        item_list = indices.tolist()
        if item_idx in item_list:
            item_list.remove(item_idx)
        return item_list


class OfflineItemSimilarity:
    def __init__(self, data_file=None, similarity_path=None, model_name='ItemCF',
                 dataset_name='Sports_and_Outdoors'):
        self.dataset_name = dataset_name
        self.similarity_path = similarity_path
        # train_data_list used for item2vec, train_data_dict used for itemCF and itemCF-IUF
        self.train_data_list, self.train_item_list, self.train_data_dict = self._load_train_data(data_file)
        self.model_name = model_name
        self.similarity_model = self.load_similarity_model(self.similarity_path)
        self.max_score, self.min_score = self.get_maximum_minimum_sim_scores()

    def get_maximum_minimum_sim_scores(self):
        max_score, min_score = -1, 100
        for item in self.similarity_model.keys():
            for neig in self.similarity_model[item]:
                sim_score = self.similarity_model[item][neig]
                max_score = max(max_score, sim_score)
                min_score = min(min_score, sim_score)
        return max_score, min_score

    def _convert_data_to_dict(self, data):
        """
        split the data set
        testdata is a test data set
        traindata is a train set
        """
        train_data_dict = {}
        for user, item, record in data:
            train_data_dict.setdefault(user, {})
            train_data_dict[user][item] = record
        return train_data_dict

    def _save_dict(self, dict_data, save_path='./similarity.pkl'):
        print("saving data to ", save_path)
        with open(save_path, 'wb') as write_file:
            pickle.dump(dict_data, write_file)

    def _load_train_data(self, data_file=None):
        """
        read the data from the data file which is a data set
        """
        train_data = []
        train_data_list = []
        train_data_set_list = []
        for line in open(data_file).readlines():
            userid, items = line.strip().split(' ', 1)
            # only use training data
            items = items.split(' ')[:-3]
            train_data_list.append(items)
            train_data_set_list += items
            for itemid in items:
                train_data.append((userid, itemid, int(1)))
        return train_data_list, set(train_data_set_list), self._convert_data_to_dict(train_data)

    def _generate_item_similarity(self, train=None, save_path='./'):
        """
        calculate co-rated users between items
        """
        print("getting item similarity...")
        train = train or self.train_data_dict
        C = dict()
        N = dict()

        if self.model_name in ['ItemCF', 'ItemCF_IUF']:
            print("Step 1: Compute Statistics")
            data_iter = tqdm(enumerate(train.items()), total=len(train.items()))
            for idx, (u, items) in data_iter:
                if self.model_name == 'ItemCF':
                    for i in items.keys():
                        N.setdefault(i, 0)
                        N[i] += 1
                        for j in items.keys():
                            if i == j:
                                continue
                            C.setdefault(i, {})
                            C[i].setdefault(j, 0)
                            C[i][j] += 1
                elif self.model_name == 'ItemCF_IUF':
                    for i in items.keys():
                        N.setdefault(i, 0)
                        N[i] += 1
                        for j in items.keys():
                            if i == j:
                                continue
                            C.setdefault(i, {})
                            C[i].setdefault(j, 0)
                            C[i][j] += 1 / math.log(1 + len(items) * 1.0)
            self.itemSimBest = dict()
            print("Step 2: Compute co-rate matrix")
            c_iter = tqdm(enumerate(C.items()), total=len(C.items()))
            for idx, (cur_item, related_items) in c_iter:
                self.itemSimBest.setdefault(cur_item, {})
                for related_item, score in related_items.items():
                    self.itemSimBest[cur_item].setdefault(related_item, 0)
                    self.itemSimBest[cur_item][related_item] = score / math.sqrt(N[cur_item] * N[related_item])
            self._save_dict(self.itemSimBest, save_path=save_path)
        elif self.model_name == 'Item2Vec':
            # details here: https://github.com/RaRe-Technologies/gensim/blob/develop/gensim/models/word2vec.py
            print("Step 1: train item2vec model")
            item2vec_model = gensim.models.Word2Vec(sentences=self.train_data_list,
                                                    vector_size=20, window=5, min_count=0,
                                                    epochs=100)
            self.itemSimBest = dict()
            total_item_nums = len(item2vec_model.wv.index_to_key)
            print("Step 2: convert to item similarity dict")
            total_items = tqdm(item2vec_model.wv.index_to_key, total=total_item_nums)
            for cur_item in total_items:
                related_items = item2vec_model.wv.most_similar(positive=[cur_item], topn=20)
                self.itemSimBest.setdefault(cur_item, {})
                for (related_item, score) in related_items:
                    self.itemSimBest[cur_item].setdefault(related_item, 0)
                    self.itemSimBest[cur_item][related_item] = score
            print("Item2Vec model saved to: ", save_path)
            self._save_dict(self.itemSimBest, save_path=save_path)
        elif self.model_name == 'LightGCN':
            # train a item embedding from lightGCN model, and then convert to sim dict
            print("generating similarity model..")
            itemSimBest = light_gcn.generate_similarity_from_light_gcn(self.dataset_name)
            print("LightGCN based model saved to: ", save_path)
            self._save_dict(itemSimBest, save_path=save_path)

    def load_similarity_model(self, similarity_model_path):
        if not similarity_model_path:
            raise ValueError('invalid path')
        elif not os.path.exists(similarity_model_path):
            print("the similirity dict not exist, generating...")
            self._generate_item_similarity(save_path=self.similarity_path)
        if self.model_name in ['ItemCF', 'ItemCF_IUF', 'Item2Vec', 'LightGCN']:
            with open(similarity_model_path, 'rb') as read_file:
                similarity_dict = pickle.load(read_file)
            return similarity_dict
        elif self.model_name == 'Random':
            similarity_dict = self.train_item_list
            return similarity_dict

    def most_similar(self, item, top_k=1, with_score=False):
        if self.model_name in ['ItemCF', 'ItemCF_IUF', 'Item2Vec', 'LightGCN']:
            """TODO: handle case that item not in keys"""
            if str(item) in self.similarity_model:
                top_k_items_with_score = sorted(self.similarity_model[str(item)].items(), key=lambda x: x[1],
                                                reverse=True)[0:top_k]
                if with_score:
                    return list(
                        map(lambda x: (int(x[0]), (self.max_score - float(x[1])) / (self.max_score - self.min_score)),
                            top_k_items_with_score))
                return list(map(lambda x: int(x[0]), top_k_items_with_score))
            elif int(item) in self.similarity_model:
                top_k_items_with_score = sorted(self.similarity_model[int(item)].items(), key=lambda x: x[1],
                                                reverse=True)[0:top_k]
                if with_score:
                    return list(
                        map(lambda x: (int(x[0]), (self.max_score - float(x[1])) / (self.max_score - self.min_score)),
                            top_k_items_with_score))
                return list(map(lambda x: int(x[0]), top_k_items_with_score))
            else:
                item_list = list(self.similarity_model.keys())
                random_items = random.sample(item_list, k=top_k)
                if with_score:
                    return list(map(lambda x: (int(x), 0.0), random_items))
                return list(map(lambda x: int(x), random_items))
        elif self.model_name == 'Random':
            random_items = random.sample(self.similarity_model, k=top_k)
            if with_score:
                return list(map(lambda x: (int(x), 0.0), random_items))
            return list(map(lambda x: int(x), random_items))


if __name__ == '__main__':
    onlineitemsim = OnlineItemSimilarity(item_size=10)
    item_embeddings = nn.Embedding(10, 6, padding_idx=0)
    onlineitemsim.update_embedding_matrix(item_embeddings)
    item_idx = torch.tensor(2, dtype=torch.long)
    similiar_items = onlineitemsim.most_similar(item_idx=item_idx, top_k=1)
    print(similiar_items)
