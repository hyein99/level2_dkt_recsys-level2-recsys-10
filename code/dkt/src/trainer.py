import math
import os

import torch
import numpy as np
import wandb
import gc

from .criterion import get_criterion
from .dataloader import get_loaders, data_augmentation
from .metric import get_metric
from .model import LSTM, LSTMATTN, Bert, LastQuery
from .optimizer import get_optimizer
from .scheduler import get_scheduler


def run(args, train_data, valid_data, model, kf_auc, kf_n=0):
    torch.cuda.empty_cache()
    gc.collect()

    # data augmentation
    augmented_train_data = data_augmentation(train_data, args)
    if len(augmented_train_data) != len(train_data):
        print(f"Data Augmentation applied. Train data {len(train_data)} -> {len(augmented_train_data)}\n")

    # train_loader, valid_loader = get_loaders(args, train_data, valid_data)
    train_loader, valid_loader = get_loaders(args, augmented_train_data, valid_data)

    # only when using warmup scheduler
    args.total_steps = int(math.ceil(len(train_loader.dataset) / args.batch_size)) * (
        args.n_epochs
    )
    args.warmup_steps = args.total_steps // 10

    optimizer = get_optimizer(model, args)
    scheduler = get_scheduler(optimizer, args)

    best_auc = -1
    best_acc = -1
    early_stopping_counter = 0
    for epoch in range(args.n_epochs):

        print(f"Start Training: Epoch {epoch + 1}")

        ### TRAIN
        train_auc, train_acc, train_loss = train(
            train_loader, model, optimizer, scheduler, args
        )

        ### VALID
        auc, acc = validate(valid_loader, model, args)

        ### TODO: model save or early stopping
        wandb.log(
            {
                "epoch": epoch,
                "train_loss_epoch": train_loss,
                "train_auc_epoch": train_auc,
                "train_acc_epoch": train_acc,
                "valid_auc_epoch": auc,
                "valid_acc_epoch": acc,
            }
        )
        if auc > best_auc:
            best_auc = auc
            # torch.nn.DataParallel로 감싸진 경우 원래의 model을 가져옵니다.
            model_to_save = model.module if hasattr(model, "module") else model

            name = args.model_name + '.pt'
            if args.split == 'k-fold':
                name = args.model_name + f'_{kf_n}.pt'
            
            # if(kf_n != 0):
            #     name = args.model_name_k_fold + f'_{kf_n}.pt'
            save_checkpoint(
                {
                    "epoch": epoch + 1,
                    "state_dict": model_to_save.state_dict(),
                },
                args.model_dir,
                name,
            )
            early_stopping_counter = 0
        else:                             # early_stopping
            early_stopping_counter += 1
            if early_stopping_counter >= args.patience:
                print(
                    f"EarlyStopping counter: {early_stopping_counter} out of {args.patience}"
                )
                break

        if acc > best_acc:
            best_acc = acc

        # scheduler
        if args.scheduler == "plateau":
            scheduler.step(best_auc)
        else:
            scheduler.step()
    
    # # save best records
    # report['best_auc'] = best_auc
    # report['best_acc'] = best_acc
    
    kf_auc.append(best_auc)
        


def train(train_loader, model, optimizer, scheduler, args):
    model.train()

    total_preds = []
    total_targets = []
    losses = []
    for step, batch in enumerate(train_loader):
        # input = list(map(lambda t: t.to(args.device), process_batch(batch)))
        input = process_batch(batch, args)
        preds = model(input)
        targets = input[-1]  # correct

        loss = compute_loss(preds, targets)
        update_params(loss, model, optimizer, scheduler, args)

        if step % args.log_steps == 0:
            print(f"Training steps: {step} Loss: {str(loss.item())}")

        # predictions
        preds = preds[:, -1]
        targets = targets[:, -1]

        total_preds.append(preds.detach())
        total_targets.append(targets.detach())
        losses.append(loss)

    total_preds = torch.concat(total_preds).cpu().numpy()
    total_targets = torch.concat(total_targets).cpu().numpy()

    # Train AUC / ACC
    auc, acc = get_metric(total_targets, total_preds)
    loss_avg = sum(losses) / len(losses)
    print(f"TRAIN AUC : {auc} ACC : {acc}")
    return auc, acc, loss_avg


def validate(valid_loader, model, args):
    model.eval()

    total_preds = []
    total_targets = []
    for step, batch in enumerate(valid_loader):
        input = process_batch(batch, args)

        preds = model(input)
        targets = input[-1]  # correct

        # predictions
        preds = preds[:, -1]
        targets = targets[:, -1]

        total_preds.append(preds.detach())
        total_targets.append(targets.detach())

    total_preds = torch.concat(total_preds).cpu().numpy()
    total_targets = torch.concat(total_targets).cpu().numpy()

    # Train AUC / ACC
    auc, acc = get_metric(total_targets, total_preds)

    print(f"VALID AUC : {auc} ACC : {acc}\n")

    return auc, acc


def inference(args, test_data, model):

    model.eval()
    _, test_loader = get_loaders(args, None, test_data)

    total_preds = []

    for step, batch in enumerate(test_loader):
        input = process_batch(batch, args)

        preds = model(input)

        # predictions
        preds = preds[:, -1]
        # preds = torch.nn.Sigmoid()(preds)
        preds = preds.cpu().detach().numpy()
        total_preds += list(preds)

    write_path = os.path.join(args.output_dir, f"submission_{args.model_name}.csv")

    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)
    with open(write_path, "w", encoding="utf8") as w:
        w.write("id,prediction\n")
        for id, p in enumerate(total_preds):
            w.write("{},{}\n".format(id, p))


def get_model(args):
    """
    Load model and move tensors to a given devices.
    """
    if args.model == "lstm":
        model = LSTM(args)
    if args.model == "lstmattn":
        model = LSTMATTN(args)
    if args.model == "bert":
        model = Bert(args)
    if args.model == "lastquery":
        model = LastQuery(args)


    return model


# 배치 전처리
def process_batch(batch, args):

    col = args.columns

    cate_batch =  {col_name : batch[args.cate_loc[col_name]] for col_name in args.cate_loc}
    conti_batch = {col_name : batch[args.conti_loc[col_name]] for col_name in args.conti_loc}

    correct = batch[col['answerCode']]
    mask = batch[-1]

    # change to float
    mask = mask.float()
    correct = correct.float()

    # interaction을 임시적으로 correct를 한칸 우측으로 이동한 것으로 사용
    interaction = correct + 1  # 패딩을 위해 correct값에 1을 더해준다.
    interaction = interaction.roll(shifts=1, dims=1)
    interaction_mask = mask.roll(shifts=1, dims=1)
    interaction_mask[:, 0] = 0
    interaction = (interaction * interaction_mask).to(torch.int64)


    # category type apply + 1, and mask
    for col_name in cate_batch:
        cate_batch[col_name] = (cate_batch[col_name] + 1 * mask).to(torch.int64).to(args.device)

    # contiuous type apply mask
    for col_name in conti_batch:
        conti_batch[col_name] = (conti_batch[col_name] * mask).to(torch.float32).to(args.device)

    # device memory로 이동
    correct = correct.to(args.device)
    mask = mask.to(args.device)
    interaction = interaction.to(args.device)

    return cate_batch, conti_batch, mask, interaction, correct
    


# loss계산하고 parameter update!
def compute_loss(preds, targets):
    """
    Args :
        preds   : (batch_size, max_seq_len)
        targets : (batch_size, max_seq_len)

    """
    loss = get_criterion(preds, targets)

    # 마지막 시퀀드에 대한 값만 loss 계산
    loss = loss[:, -1]
    # loss = torch.gather(loss, 1, index)
    loss = torch.mean(loss)
    return loss


def update_params(loss, model, optimizer, scheduler, args):
    loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip_grad)
    if args.scheduler == "linear_warmup":
        scheduler.step()
    optimizer.step()
    optimizer.zero_grad()


def save_checkpoint(state, model_dir, model_filename):
    print("saving model ...\n")
    if not os.path.exists(model_dir):
        os.makedirs(model_dir)
    print(model_filename)
    torch.save(state, os.path.join(model_dir, model_filename))


def load_model(args, idx):
    if args.split == 'user':
        model_path = os.path.join(args.model_dir, args.model_name + '.pt')
    elif args.split == 'k-fold':
        model_path = os.path.join(args.model_dir, args.model_name + f'_{idx}.pt')
    print("Loading Model from:", model_path)
    load_state = torch.load(model_path)
    model = get_model(args)

    # load model state
    model.load_state_dict(load_state["state_dict"], strict=True)

    print("Loading Model from:", model_path, "...Finished.")
    return model
