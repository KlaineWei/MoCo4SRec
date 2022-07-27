# Reference

Please cite our paper if you use this code.

```
@article{wei2022contrastive,
  title={Contrastive self-supervised sequential recommendation with robust augmentation},
  author={Liu, Zhiwei and Chen, Yongjun and Li, Jia and Yu, Philip S and McAuley, Julian and Xiong, Caiming},
  journal={arXiv preprint arXiv:2108.06479},
  year={2021}
}
```

# Implementation
## Requirements

Python >= 3.7  
Pytorch >= 1.2.0  
tqdm == 4.26.0

## Datasets

Four prepared datasets are included in `data` folder.

## Train Model

To train MoCo4SRec on `Sports_and_Outdoors` dataset, change to the `src` folder and run following command: 

```
bash sports.sh
```
You can train MoCo4SRec on Beauty or Yelp in a similar way.

The script will automatically train MoCo4SRec and save the best model found in validation set, and then evaluate on test set


# Acknowledgement
 - Transformer and training pipeline are implemented based on [CoSeRec](https://github.com/YChen1993/CoSeRec). Thanks them for providing efficient implementation.

