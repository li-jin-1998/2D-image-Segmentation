import os

os.environ['CUDA_VISIBLE_DEVICES'] = '1'

import time
import datetime
import torch

from utils.train_and_eval import train_one_epoch, evaluate, create_lr_scheduler
from dataset import MyDataset
from parse_args import parse_args, get_model


def train():
    args = parse_args()

    device = torch.device(args.device if torch.cuda.is_available() else "cpu")
    batch_size = args.batch_size
    num_classes = args.num_classes + 1

    # 用来保存训练以及验证过程中信息
    results_file = "log/{}_{}.txt".format(args.arch, datetime.datetime.now().strftime("%Y%m%d-%H%M"))

    train_dataset = MyDataset(args.data_path + "/train", args.image_size)
    val_dataset = MyDataset(args.data_path + "/test", args.image_size)
    # train_dataset = MyDataset(args.data_path+"/augmentation_train", args.image_size)
    # val_dataset = MyDataset(args.data_path + "/augmentation_test", args.image_size)

    num_workers = min([os.cpu_count(), batch_size if batch_size > 1 else 0, 8])
    train_loader = torch.utils.data.DataLoader(train_dataset,
                                               batch_size=batch_size,
                                               num_workers=num_workers,
                                               shuffle=True,
                                               pin_memory=True)

    val_loader = torch.utils.data.DataLoader(val_dataset,
                                             batch_size=batch_size,
                                             num_workers=num_workers,
                                             pin_memory=True)

    model = get_model(args)
    # for k, v in model.named_parameters():
    #     # print("当前参数名称 {}".format(k))
    #     v.requires_grad = True
    #     for i in range(0, 4):
    #         if k.startswith("backbone."+str(i)):
    #             print('freezing {}'.format(k))
    #             v.requires_grad = False
    # summary(model, (3, args.image_size, args.image_size))
    # exit(0)
    params_to_optimize = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.Adamax(params_to_optimize, lr=args.lr)
    # optimizer = torch.optim.SGD(params_to_optimize,lr=args.lr, momentum=0.9, weight_decay=1e-4)

    scaler = torch.cuda.amp.GradScaler() if args.amp else None

    # 创建学习率更新策略，这里是每个step更新一次(不是每个epoch)
    # lr_scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, last_epoch=-1, gamma=0.95, verbose=True)
    lr_scheduler = create_lr_scheduler(optimizer, len(train_loader), args.epochs, warmup=False, warmup_epochs=1)

    if args.multi_scale:
        weights_path = "save_weights/{}_latest_model.pth".format(args.arch)
        model.load_state_dict(torch.load(weights_path, map_location='cpu'))
        print(">" * 10, 'load last weight:', weights_path)

    if args.resume:
        weights_path = "save_weights/{}_best_model.pth".format(args.arch)
        checkpoint = torch.load(weights_path, map_location='cpu')
        model.load_state_dict(checkpoint['model'])
        optimizer.load_state_dict(checkpoint['optimizer'])
        lr_scheduler.load_state_dict(checkpoint['lr_scheduler'])
        args.start_epoch = checkpoint['epoch']
        if args.amp:
            scaler.load_state_dict(checkpoint["scaler"])
        print(">" * 10, 'load best weight:', weights_path)
    best_dice = 0.
    best_miou = 0.
    best_epoch = 1
    start_time = time.time()
    lr = args.lr
    train_losses = []
    val_losses = []
    dices = []
    mious = []
    for epoch in range(args.start_epoch, args.epochs + 1):
        print('-' * 20)
        print('Epoch {}/{} lr {:.6f}'.format(epoch, args.epochs, lr))
        print('-' * 20)
        train_loss, lr = train_one_epoch(epoch, model, optimizer, train_loader, device, num_classes,
                                         lr_scheduler=lr_scheduler, scaler=scaler)
        confmat, val_dice, val_loss, val_miou = evaluate(epoch, model, val_loader, device=device,
                                                         num_classes=num_classes)
        print("train loss:{:.4f}".format(train_loss))
        print("val loss:{:.4f}".format(val_loss))
        val_info = str(confmat)
        # print(val_info)
        print("val dice:{:.2f}".format(val_dice * 100))
        print("val miou:{:.2f}".format(val_miou * 100))
        train_losses.append(train_loss)
        val_losses.append(val_loss)
        dices.append(val_dice)
        mious.append(val_miou)
        # write into txt
        with open(results_file, "a") as f:
            # 记录每个epoch对应的train_loss、lr以及验证集各指标
            train_info = f"[epoch: {epoch}]\n" \
                         f"train_loss: {train_loss:.4f}\n" \
                         f"val_loss: {val_loss:.4f}\n" \
                         f"lr: {lr:.6f}\n" \
                         f"dice: {val_dice * 100:.2f}\n"
            f.write(train_info + val_info + "\n\n")
        torch.save(model.state_dict(), "save_weights/{}_latest_model.pth".format(args.arch))
        if args.save_best is True:
            if best_miou <= val_miou:
                best_miou = val_miou
                best_dice = val_dice
                best_epoch = epoch
                print("best epoch:{} dice:{:.2f} miou:{:.2f}".format(best_epoch, best_dice * 100, best_miou * 100))
            else:
                print("best epoch:{} dice:{:.2f} miou:{:.2f}".format(best_epoch, best_dice * 100, best_miou * 100))
                continue
        save_file = {"model": model.state_dict(),
                     "optimizer": optimizer.state_dict(),
                     "lr_scheduler": lr_scheduler.state_dict(),
                     "epoch": epoch,
                     "args": args}
        if args.amp:
            save_file["scaler"] = scaler.state_dict()

        if args.save_best is True:
            torch.save(save_file, "save_weights/{}_best_model.pth".format(args.arch))
        else:
            torch.save(save_file, "save_weights/model_{}.pth".format(epoch))
    with open(results_file, "a") as f:
        best_info = f"[epoch: {best_epoch}]\n" \
                    f"best_dice: {best_dice * 100:.2f}\n" \
                    f"best_miou: {best_miou * 100:.2f}\n"
        f.write(best_info)

    from plot import loss_plot, metrics_plot
    loss_plot(args, train_losses, val_losses)
    metrics_plot(args, "dice", dices)
    metrics_plot(args, "miou", mious)

    total_time = time.time() - start_time
    total_time_str = str(datetime.timedelta(seconds=int(total_time)))
    print("training time {}".format(total_time_str))


if __name__ == '__main__':
    if not os.path.exists("./save_weights"):
        os.mkdir("./save_weights")
    train()
